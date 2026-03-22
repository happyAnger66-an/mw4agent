"""Setup script for MW4Agent"""

from setuptools import setup, find_packages

setup(
    name="mw4agent",
    version="0.1.0",
    description="Multi-WebSocket Agent CLI",
    packages=find_packages(),
    install_requires=[
        "click>=8.0.0",
        "fastapi>=0.110.0",
        "uvicorn>=0.27.0",
        "cryptography>=42.0.0",
        "httpx>=0.26.0",
        "pytest-asyncio>=0.21.0",
        "lark-oapi>=1.5.0",
        "PyYAML>=6.0",
        "questionary>=2.0.0",
        "websockets>=12.0",
    ],
    entry_points={
        "console_scripts": [
            "mw4agent=mw4agent.cli.main:main",
        ],
    },
    python_requires=">=3.8",
    # When HTTP_PROXY / HTTPS_PROXY use socks5:// or socks://, httpx needs socksio.
    extras_require={
        "socks": ["socksio>=1.0.0,<2"],
    },
    # Ensure dashboard SPA static assets are installed with the package so that
    # FastAPI's StaticFiles mount in mw4agent.gateway.server can always find them.
    include_package_data=True,
    package_data={
        "mw4agent": [
            "dashboard/static/index.html",
            "dashboard/static/app.js",
            "dashboard/static/i18n.js",
            "dashboard/static/theme.js",
        ],
    },
)
