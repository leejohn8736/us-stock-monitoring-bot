from setuptools import find_packages, setup

setup(
    name="us_market_bot",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "requests",
        "python-telegram-bot",
        "pytz",
    ],
)
