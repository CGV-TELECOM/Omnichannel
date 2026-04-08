BillingBE/
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   ├── endpoints/
│   │   │   └── __init__.py
│   │   └── __init__.py
│   ├── core/
│   │   ├── config/
│   │   │   ├── app_config.py        # Cấu hình ứng dụng
│   │   │   ├── database.py          # Cấu hình database
│   │   │   └── logging.py           # Cấu hình logging
│   │   ├── security/
│   │   │   ├── auth.py              # Xử lý authentication
│   │   │   ├── jwt.py               # JWT handling
│   │   │   └── permissions.py       # Phân quyền
│   │   └── __init__.py
│   ├── db/
│   │   ├── models/                  # SQLAlchemy models
│   │   ├── repositories/            # Database repositories
│   │   ├── migrations/              # Alembic migrations
│   │   └── session.py               # Database session
│   ├── schemas/
│   │   ├── requests/                # Pydantic request models
│   │   ├── responses/               # Pydantic response models
│   │   └── __init__.py
│   ├── services/
│   ├── utils/
│   │   ├── constants.py
│   │   ├── decorators.py
│   │   ├── helpers.py
│   │   └── validators.py
│   ├── middleware/
│   │   ├── auth_middleware.py
│   │   ├── error_handler.py
│   │   ├── logging_middleware.py
│   │   └── rate_limiter.py
│   └── __init__.py
├── tests/
│   ├── conftest.py                  # Test configurations
│   ├── integration/                 # Integration tests
│   ├── unit/                        # Unit tests
│   └── __init__.py
├── scripts/
│   ├── backup.sh
│   └── deploy.sh
├── logs/                            # Log files
├── docs/                            # Documentation
│   ├── api/
│   └── setup/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── main.py
├── pyproject.toml                   # Project metadata and dependencies
├── README.md
└── requirements.txt