def classFactory(iface):
    from .plugin import TuflowToolsPlugin
    return TuflowToolsPlugin(iface)