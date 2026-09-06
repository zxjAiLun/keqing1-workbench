from typing import Any

HOST: str = '0.0.0.0'
# Keep the owned gateway outside both this workstation's configured Windows
# dynamic TCP range (1024-15000) and the Windows default range
# (49152-65535).  Play-with-you may try the following PORT_RANGE_SIZE ports
# when an application has explicitly claimed one of them.
PORT: int = 21600
PORT_RANGE_SIZE: int = 32
SEX: str = 'M'
DEBUG: bool = True
LOGGING: dict[str, Any] = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s'
        }
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'formatter': 'simple'
        }
    },
    'loggers': {
        '__main__': {
            'handlers': ['file'],
            'level': 'DEBUG'
        },
        'responder': {
            'handlers': ['file'],
            'level': 'DEBUG'
        },
        'websockets': {
            'handlers': ['console'],
            'level': 'DEBUG'
        }
    }
}
