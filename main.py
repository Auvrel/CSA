import flet as ft

# Import the main application logic from flet_app.py
from gui.flet_app import main

if __name__ == "__main__":
    # The 'target' function for ft.app() is now imported from flet_app.py
    ft.app(target=main)