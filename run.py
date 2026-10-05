# -*- coding: utf-8 -*-
"""
LNMU Result Updater 2.0 — Startup Script
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_new import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    
    print(f"""
╔════════════════════════════════════════════════════════════╗
║         LNMU Result Updater 2.0 - Starting Server          ║
╠════════════════════════════════════════════════════════════╣
║  Server: http://0.0.0.0:{port}                           ║
║  Debug:  {debug}                                             ║
║  Version: 2.0.0                                             ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    app.run(host='0.0.0.0', port=port, debug=debug)