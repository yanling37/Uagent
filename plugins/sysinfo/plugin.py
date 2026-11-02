"""Example plugin. Requires explicit trust in the app before loading."""
import platform
from portable_ai.tools import ToolSpec, schema


def register():
    return [ToolSpec('system_info', 'Read platform and Python version without changing the computer.',
                     schema({}), lambda: {
                         'os': platform.system(),
                         'architecture': platform.machine(),
                         'python': platform.python_version(),
                     })]
