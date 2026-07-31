APP_QSS = """
QMainWindow {
    background: #dfe8f2;
}
QWidget {
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 9pt;
    color: #172331;
}
QFrame#TopBand {
    background: #e9f0f7;
    border-bottom: 1px solid #b8c4d0;
}
QFrame#LeftPanel {
    background: #eef3f8;
    border-right: 1px solid #9aa9b8;
}
QLabel#SectionTitle {
    background: #d8e5f0;
    border-top: 1px solid #aebcca;
    border-bottom: 1px solid #aebcca;
    font-weight: 600;
    padding: 3px 6px;
}
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: white;
    border: 1px solid #b7c4cf;
    padding: 2px;
}
QPushButton#ConnectButton {
    background: transparent;
    border: 0;
    font-weight: 600;
}
QPushButton#StartButton, QPushButton#StopButton {
    background: #e7edf3;
    border: 1px solid #aab8c5;
    border-radius: 4px;
    min-width: 44px;
    min-height: 38px;
    font-weight: 600;
}
QPushButton#StartButton:hover, QPushButton#StopButton:hover {
    background: #f4f8fc;
}
QPushButton#StopButton:disabled, QPushButton#StartButton:disabled {
    color: #8d98a4;
}
QLabel#StatusBad {
    color: #b12a2a;
    font-weight: 600;
}
QLabel#StatusGood {
    color: #217b3b;
    font-weight: 600;
}
QLabel#SmallMuted {
    color: #566778;
}
QFrame#WaterfallFrame {
    background: black;
    border: 1px solid #8795a4;
}
QStatusBar {
    background: #eef3f8;
    border-top: 1px solid #b8c4d0;
}
"""
