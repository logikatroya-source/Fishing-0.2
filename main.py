import sys
from PySide6.QtWidgets import QApplication
from fishing.ui import Main

def main():
    app=QApplication(sys.argv)
    app.setApplicationName('GamePilot Fishing')
    w=Main();w.show()
    return app.exec()

if __name__=='__main__':
    raise SystemExit(main())
