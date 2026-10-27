# BL Network

**Децентрализованный блокчейн на Python: PoS, смарт-контракты (Tolk/TVM), DNS-домены `.bl`.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-proof%20of%20concept-orange)]()

---

## О проекте

**BL Network** — работающий децентрализованный блокчейн, вдохновлённый архитектурой **TON** и **Ethereum**. Он включает:

- **PoS-консенсус** с ротацией валидаторов по стейку.
- **Смарт-контракты** на языке **Tolk**, компилируемые в байт-код для **TVM** (виртуальной машины на ячейках).
- **P2P-сеть** для синхронизации блоков и транзакций.
- **HTTP API** для кошелька, эксплорера и внешних сервисов.
- **DNS-домены `.bl`** с регистрацией через открытый аукцион.

Проект создан в образовательных целях и является **proof-of-concept**. Не предназначен для хранения реальных активов.

## Возможности

- **Криптография**: Ed25519-подписи транзакций и блоков, адреса `BLC…` с checksum.
- **Консенсус**: PoS с ротацией производителей блоков на основе стейка.
- **Смарт-контракты**: язык Tolk (функции, арифметика, сравнения) → TVM (газ, ячейки, стек-машина).
- **Транспорт**: TCP P2P с обменом `HELLO`, `GET_CHAIN`, `CHAIN`, `NEW_TX`, `NEW_BLOCK`.
- **Экономика**: Faucet (100 BLC один раз), стейкинг (мин. 100 BLC), награда за блок (10 BLC), отложенный вывод стейка (1 час).
- **DNS**: прямая регистрация (0.1 BLC/год) и аукцион (3 минуты, анти-снайп окно 30 сек).
- **API**: `/status`, `/balance/<addr>`, `/validators`, `/contracts`, `/dns/*`, `/send`, `/call`.
- **CLI-кошелёк**: `create`, `faucet`, `stake`, `unstake`, `send`, `deploy`, `call`, `dns-register`, `auction-*`.

## Установка

### Требования

- Python 3.10+
- Linux (Ubuntu 22.04 / 24.04 рекомендуется)

### Шаги

```bash
git clone https://github.com/blks-org/bl-network.git
cd bl-network
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m unittest discover tests -v
```

Если тесты прошли — установка успешна.

## Запуск одного узла (dev)

```bash
# 1. Сгенерировать genesis для одного валидатора
python -m bl.tools 1 --out genesis

# 2. Запустить узел
python -m bl.node \
  --data-dir data \
  --key genesis/validator-0.priv \
  --genesis genesis/genesis.json \
  --port 7001 --api-port 8001
```

После запуска откройте `http://localhost:8001/status` — увидите `height`, который растёт.

## Запуск сети из 3 узлов (VDS)

**На сервере №1:**

```bash
python -m bl.tools 3 --out genesis
```

Скопируйте `genesis/genesis.json` на **все** серверы, а `validator-{0,1,2}.priv` — по одному на каждый.

**На каждом сервере:**

```bash
python -m bl.node \
  --data-dir data \
  --key data/validator.priv \
  --genesis data/genesis.json \
  --port 7001 --api-port 8001 \
  --peers IP_СЕРВЕРА_2:7001 \
  --peers IP_СЕРВЕРА_3:7001
```

Через 10–15 секунд все узлы покажут одинаковый `height` и `head` — консенсус работает.

## Роли в сети

| Роль | Что делает |
|---|---|
| **Нода (Node)** | Хранит копию блокчейна, транслирует транзакции и блоки. Может быть валидатором или просто наблюдателем. |
| **Валидатор (Validator)** | Нода с активным стейком ≥ 100 BLC. Создаёт блоки в свою очередь и получает награду 10 BLC. |
| **Майнер (Miner)** | **В PoS отсутствует.** Функцию майнера выполняет валидатор. Нет PoW-хашинга. |
| **Стейк (Stake)** | Залог. Чем больше стейк, тем чаще очередь создавать блок. Вывод стейка — с задержкой 1 час. |

## Как стать валидатором

```bash
# 1. Создать кошелёк
python -m bl.wallet create --out me.json

# 2. Получить тестовые монеты (faucet)
python -m bl.wallet faucet --file me.json

# 3. Застейкать минимум 100 BLC
python -m bl.wallet stake --file me.json --amount 100

# 4. Проверить, что вы в списке активных валидаторов
curl http://localhost:8001/validators
```

> **Важно:** чтобы создавать блоки, ваш адрес должен быть в `genesis.json`. В текущей версии нет автоматического голосования за добавление новых валидаторов — это есть в Roadmap.

## Смарт-контракты

### Пример контракта на Tolk (`counter.tolk`)

```tolk
fun add(a: int, b: int): int {
    return a + b;
}

fun mul(a: int, b: int): int {
    return a * b;
}
```

### Деплой

```bash
python -m bl.wallet deploy --file me.json --code counter.tolk
```

### Вызов (read-only)

```bash
curl -X POST http://localhost:8001/call \
  -H 'Content-Type: application/json' \
  -d '{"address":"BLCX...","method":"add","args":[2,3]}'
# → {"ok":true,"returned":5,"gas_used":4}
```

### Вызов (изменяющий состояние)

```bash
python -m bl.wallet call --file me.json \
  --address BLCX... --method mul --args "5,7"
```

## DNS-домены

```bash
# Прямая регистрация (0.1 BLC)
python -m bl.wallet dns-register --file me.json --name mysite

# Проверка
curl http://localhost:8001/dns/resolve/mysite
```

### Аукцион

```bash
python -m bl.wallet auction-start --file me.json --name cool --bid 100
python -m bl.wallet auction-bid --file me.json --name cool --bid 105
# подождать 3 минуты
python -m bl.wallet auction-settle --file me.json --name cool
# если вас перебили — забрать refund:
python -m bl.wallet auction-refund --file me.json --name cool
```

## HTTP API

| Метод | Endpoint | Описание |
|---|---|---|
| `GET` | `/status` | Высота, head, валидаторы, mempool |
| `GET` | `/balance/<addr>` | Баланс, nonce, стейк |
| `GET` | `/validators` | Активные валидаторы |
| `GET` | `/contracts` | Все задеплоенные контракты |
| `GET` | `/dns/domains` | Все домены `.bl` |
| `GET` | `/dns/resolve/<name>` | Разрешить имя домена |
| `POST` | `/send` | Отправить подписанную транзакцию |
| `POST` | `/call` | Read-only вызов метода контракта |

## Структура репозитория

```
bl-network/
├── bl/
│   ├── __init__.py
│   ├── config.py
│   ├── core/           # Блокчейн, транзакции, блоки, криптография
│   ├── vm/             # TVM: ячейки, стек-машина, компилятор Tolk
│   ├── network/        # P2P транспорт
│   ├── api.py          # HTTP RPC
│   ├── node.py         # Демон узла
│   ├── wallet.py       # CLI-кошелёк
│   └── tools.py        # Генератор genesis
├── deploy/             # systemd-юнит
├── tests/              # Unit-тесты
├── requirements.txt
├── LICENSE
└── README.md
```

## Roadmap

- [x] PoS-консенсус (ротация по стейку)
- [x] Смарт-контракты на Tolk → TVM
- [x] P2P-синхронизация (longest-chain)
- [x] DNS-домены + аукцион
- [x] Faucet + стейкинг + награда за блок
- [ ] Slashing (штраф за неактивность/вредоносное поведение)
- [ ] Fork-choice (выбор цепи по накопленному стейку, а не только longest-chain)
- [ ] Автоматическое добавление валидаторов по стейку + голосование
- [ ] Расширение Tolk: `if/else`, циклы, структуры, persistent storage
- [ ] Light client
- [ ] DHT / peer discovery

## Лицензия

Проект распространяется под лицензией **MIT**. См. файл [LICENSE](LICENSE).

## Контакты

- Issues: [github.com/blks-org/bl-network/issues](https://github.com/blks-org/bl-network/issues)
