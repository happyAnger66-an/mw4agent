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
    ],
    entry_points={
        "console_scripts": [
            "mw4agent=mw4agent.cli.main:main",
        ],
    },
    python_requires=">=3.8",
)
