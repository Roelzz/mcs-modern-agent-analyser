import os

from dotenv import load_dotenv
import reflex as rx

load_dotenv()

_env = os.getenv("REFLEX_ENV", "dev")

if _env == "prod":
    _port = int(os.getenv("PORT", "2009"))
    _port_cfg = {"frontend_port": _port, "backend_port": _port}
else:
    _port_cfg = {
        "frontend_port": int(os.getenv("FRONTEND_PORT", "3000")),
        "backend_port": int(os.getenv("BACKEND_PORT", "8000")),
    }

_state_manager_mode = (
    rx.constants.StateManagerMode.REDIS if os.getenv("REDIS_URL") else rx.constants.StateManagerMode.MEMORY
)

config = rx.Config(
    app_name="web",
    state_manager_mode=_state_manager_mode,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                accent_color="grass",
                gray_color="sand",
                radius="large",
                appearance="inherit",
            )
        ),
    ],
    **_port_cfg,
)
