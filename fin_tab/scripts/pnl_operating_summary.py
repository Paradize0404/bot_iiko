import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

# Allow running as a script without installing the package
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

# Типы ПиУ для группировки
REVENUE_TYPES = {"income"}
PROD_TYPES = {"costprice", "direct-production", "direct-variable"}
INDIRECT_TYPES = {"general-production", "administrative", "commercial"}


def _fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _parse_args(argv: Iterable[str]) -> tuple[str, int | None, bool]:
    # Ожидаем: <MM.YYYY> [directionId] [--verbose]; по умолчанию — текущий месяц, все направления
    args = list(argv)
    if args:
        date_arg = args[0]
    else:
        now = datetime.now()
        date_arg = f"{now:%m.%Y}"

    direction_id = int(args[1]) if len(args) > 1 and args[1] and not args[1].startswith("--") else None
    verbose = any(arg in {"--verbose", "-v"} for arg in args[1:])
    return date_arg, direction_id, verbose


async def fetch_summary(date_str: str, direction_id: int | None, verbose: bool) -> None:
    # Подтягиваем токены из .env
    load_dotenv(Path.cwd() / ".env")

    async with FinTabloClient() as cli:
        categories = await cli.list_pnl_categories()
        cat_to_type = {c["id"]: c.get("pnlType") for c in categories}

        params = {"date": date_str}
        if direction_id is not None:
            params["directionId"] = direction_id

        items = await cli.list_pnl_items(**params)
        agg = defaultdict(float)
        unknown = defaultdict(float)
        by_category = defaultdict(float)

        for it in items:
            cat_id = it.get("categoryId")
            val = float(it.get("value", 0) or 0)
            pnl_type = cat_to_type.get(cat_id)
            if pnl_type is None:
                unknown[cat_id] += val
            else:
                agg[pnl_type] += val
                by_category[cat_id] += val

        revenue = sum(val for t, val in agg.items() if t in REVENUE_TYPES)
        prod = sum(val for t, val in agg.items() if t in PROD_TYPES)
        indirect = sum(val for t, val in agg.items() if t in INDIRECT_TYPES)
        operating_profit = revenue - prod - indirect

        print(f"Дата: {date_str}")
        if direction_id is not None:
            print(f"Фильтр по directionId: {direction_id}")
        print(f"Выручка (income): {_fmt(revenue)}")
        print(f"Производственные расходы (costprice/direct-production/direct-variable): {_fmt(prod)}")
        print(f"Косвенные расходы (general-production/administrative/commercial): {_fmt(indirect)}")
        print(f"Операционная прибыль: {_fmt(operating_profit)}")

        if unknown:
            print("Записи с неизвестным pnlType (нет категории):")
            for cat, val in unknown.items():
                print(f"  categoryId={cat}: {_fmt(val)}")

        if verbose:
            print("\nДетализация по pnlType:")
            for key in sorted(agg.keys()):
                print(f"  {key}: {_fmt(agg[key])}")

            if by_category:
                print("\nТоп категорий по сумме:")
                for cat_id, val in sorted(by_category.items(), key=lambda kv: abs(kv[1]), reverse=True)[:20]:
                    print(f"  categoryId={cat_id}: {_fmt(val)} (pnlType={cat_to_type.get(cat_id)})")


def main() -> None:
    date_str, direction_id, verbose = _parse_args(iter(__import__("sys").argv[1:]))
    asyncio.run(fetch_summary(date_str, direction_id, verbose))


if __name__ == "__main__":
    main()
