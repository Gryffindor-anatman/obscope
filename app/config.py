import os


class Settings:
    SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "demo-api")

    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

    MYSQL_HOST = os.getenv("MYSQL_HOST", "host.docker.internal")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DB = os.getenv("MYSQL_DB", "demoapp")

    HTTPBIN_URL = os.getenv("HTTPBIN_URL", "http://host.docker.internal:80")


settings = Settings()
