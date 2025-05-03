import os
import sys
import logging

def is_frozen():
    return globals().get("__compiled__", False) or hasattr(sys, 'frozen') or hasattr(sys, '_MEIPASS')

def get_base_path():
    if is_frozen():
        # PyInstaller sets _MEIPASS, Nuitka onefile uses sys.executable's dir
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return os.path.dirname(sys.executable)
    else:
        # Go up two levels from UI/src/models to UI
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

def get_templates_dir():
    path = os.path.join(get_base_path(), 'templates')
    logging.getLogger(__name__).info(f"[appdirs] get_templates_dir resolved to: {path}")
    return path

def get_static_dir():
    path = os.path.join(get_base_path(), 'static')
    logging.getLogger(__name__).info(f"[appdirs] get_static_dir resolved to: {path}")
    return path

def get_resource_dir():
    return os.path.join(get_base_path(), 'assets')

def resource_path(relative_path):
    try:
        # First try the normal path
        base_path = get_resource_dir()
        path = os.path.join(base_path, relative_path)
        if os.path.exists(path):
            return path
            
        # If not found and we're in a frozen app, try alternate locations
        if is_frozen():
            # Try direct from executable directory
            alt_path = os.path.join(os.path.dirname(sys.executable), 'assets', relative_path)
            if os.path.exists(alt_path):
                return alt_path
                
            # Try in the same directory as the executable
            alt_path2 = os.path.join(os.path.dirname(sys.executable), relative_path)
            if os.path.exists(alt_path2):
                return alt_path2
        
        # If we got here and still didn't find it, return the original path
        return path
    except Exception as e:
        print(f"ERROR - Failed to load resource: {e}")
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
    return output_dir

def get_logs_dir():
    logs_dir = os.path.join(get_appdata_dir(), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir

def get_settings_file():
    return os.path.join(get_appdata_dir(), 'settings.json')

def get_capture_state_file():
    return os.path.join(get_appdata_dir(), 'capture_state.json')
