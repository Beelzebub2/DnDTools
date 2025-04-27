import os
import sys

def is_frozen():
    return globals().get("__compiled__", False)

def get_base_path():
    if is_frozen():
        return os.path.dirname(sys.executable)
    else:
        # Go up two levels from UI/src/models to UI
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

def get_templates_dir():
    return os.path.join(get_base_path(), 'templates')

def get_static_dir():
    return os.path.join(get_base_path(), 'static')

def get_resource_dir():
    return os.path.join(get_base_path(), 'assets')

def resource_path(relative_path):
    return os.path.join(get_resource_dir(), relative_path)

def get_appdata_dir():
    appdata = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~\\AppData\\Local')
    dndtools_dir = os.path.join(appdata, 'DnDTools')
    os.makedirs(dndtools_dir, exist_ok=True)
    return dndtools_dir

def get_data_dir():
    data_dir = os.path.join(get_appdata_dir(), 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_output_dir():
    output_dir = os.path.join(get_appdata_dir(), 'output')
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def get_logs_dir():
    logs_dir = os.path.join(get_appdata_dir(), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir

def get_settings_file():
    return os.path.join(get_appdata_dir(), 'settings.json')

def get_capture_state_file():
    return os.path.join(get_appdata_dir(), 'capture_state.json')
