import uvicorn

from .app import create_app
from .settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.service_host, port=settings.service_port)


if __name__ == "__main__":
    main()
