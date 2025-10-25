# main.py - start the GUI app
# run: python main.py

import sys
from PyQt5 import QtWidgets
from gui.app import MainWindow

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())