"""File discovery providers. Each adapter module is imported only when a configured provider uses it."""

import importlib

MODULES = {"fixture": "fixture", "recoll": "recoll", "sist2": "sist2", "manifest": "manifest"}


def build(provider_config, files_config):
    module = importlib.import_module(f"towpath.discovery.providers.{MODULES[provider_config.adapter]}")
    return module.Provider(provider_config, files_config)
