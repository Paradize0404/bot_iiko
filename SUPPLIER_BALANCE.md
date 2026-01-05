# Баланс по поставщикам

## Описание

Модуль для получения баланса по поставщикам через OLAP отчет по проводкам (TRANSACTIONS).

## Что показывает отчет

- **Отгружено** (`Sum.Outgoing`) - сколько товаров ушло со склада (расход)
- **Приход** (`Sum.Incoming`) - сколько поступило от поставщика (приход)  
- **Баланс** (`Sum`) - итоговый баланс расчетов с поставщиком
  - ✅ **Положительный баланс** = МЫ ДОЛЖНЫ поставщику
  - ⚠️ **Отрицательный баланс** = поставщик ДОЛЖЕН нам

## Как работает

Отчет делается **НА ДАТУ** (не на период!), используя OLAP отчет по проводкам с группировкой по `Counteragent.Name` и фильтром `Counteragent=SUPPLIER`.

Баланс рассчитывается от начала времен (01.01.2020) до указанной даты.

## Использование

### Из кода

```python
from services.supplier_balance import get_supplier_balance, format_supplier_balance_report

# Получить баланс на конкретную дату
balance_data = await get_supplier_balance("23.12.2025")
# или
balance_data = await get_supplier_balance("2025-12-23")

# Форматировать в текстовый отчет
report = format_supplier_balance_report(balance_data)
print(report)

# Работа с данными
for supplier in balance_data['suppliers']:
    print(f"{supplier['name']}: баланс {supplier['balance']}₽")
```

### Из консоли

```bash
python test_supplier_balance_service.py
```

## Структура данных

```python
{
    'date': '23.12.2025',  # дата отчета
    'suppliers': [  # список поставщиков
        {
            'name': 'ООО Поставщик',
            'outgoing': Decimal('1000.00'),  # отгружено
            'incoming': Decimal('1200.00'),  # приход
            'balance': Decimal('200.00')     # баланс (мы должны)
        },
        ...
    ],
    'total_outgoing': Decimal('42199107.32'),
    'total_incoming': Decimal('42199107.32'),
    'total_balance': Decimal('0.00'),
    'debt_to_suppliers': Decimal('3140926.25'),    # наша задолженность
    'debt_from_suppliers': Decimal('3140926.25')  # должны нам
}
```

## Примеры

### Топ-5 должников перед нами

```python
balance_data = await get_supplier_balance()
debtors = [s for s in balance_data['suppliers'] if s['balance'] < 0]
debtors.sort(key=lambda x: x['balance'])  # от меньшего к большему
top_5 = debtors[:5]

for supplier in top_5:
    print(f"{supplier['name']}: должен нам {abs(supplier['balance']):,.2f}₽")
```

### Топ-5 поставщиков, которым мы должны

```python
balance_data = await get_supplier_balance()
creditors = [s for s in balance_data['suppliers'] if s['balance'] > 0]
creditors.sort(key=lambda x: x['balance'], reverse=True)
top_5 = creditors[:5]

for supplier in top_5:
    print(f"{supplier['name']}: мы должны {supplier['balance']:,.2f}₽")
```

## API параметры

OLAP запрос использует следующие параметры:

- `report=TRANSACTIONS` - отчет по проводкам
- `from=01.01.2020` - начало периода (от начала времен)
- `to=23.12.2025` - конец периода (указанная дата)
- `groupRow=Counteragent.Name` - группировка по имени контрагента
- `agr=Sum.Outgoing` - агрегация по сумме расхода
- `agr=Sum.Incoming` - агрегация по сумме прихода
- `agr=Sum` - агрегация по общей сумме (баланс)
- `Counteragent=SUPPLIER` - фильтр только поставщики

## Файлы

- `services/supplier_balance.py` - основной модуль
- `test_supplier_balance_service.py` - тест и пример использования
- `test_supplier_balance_clean.py` - консольная версия с детальным выводом
- `test_supplier_balance.py` - отладочная версия (показывает все доступные поля)
