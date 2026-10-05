"""Standalone scripts run by another interpreter (for example the one that has Recoll's binding).

They import nothing from Towpath. This folder must hold no module whose name
could shadow the tool's own package (such as ``recoll``), because a script's
folder comes first on its import path.
"""
