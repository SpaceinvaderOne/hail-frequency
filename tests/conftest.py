import os
import sys

# Image layout: WORKDIR /app, package at /app/app, tests at /app/tests.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import inspect

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio_inline: inline async runner")


def pytest_collection_modifyitems(items):
    for item in items:
        if inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio_inline)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    fn = pyfuncitem.function
    if inspect.iscoroutinefunction(fn):
        kwargs = {n: pyfuncitem.funcargs[n]
                  for n in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(fn(**kwargs))
        return True
