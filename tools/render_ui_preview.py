"""Reproducible synthetic UI snapshots, never connects to the mailbox."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication, QTextBrowser
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import QTimer
from coremail_desktop.ui.main_window import MainWindow
from coremail_desktop.ui.demo import DemoController
from coremail_desktop.ui.theme import apply_theme


def main():
    app = QApplication([])
    # The offscreen plugin has no Windows platform font discovery.
    for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if font.exists(): QFontDatabase.addApplicationFont(str(font))
    output = Path('analysis')
    output.mkdir(exist_ok=True)
    apply_theme(app)
    controller = DemoController()
    window = MainWindow(controller, reader_factory=QTextBrowser, demo=True)
    window.show()
    controller.populate(window)

    def capture():
        window.mail_list.view.setCurrentIndex(window.mail_list.model.index(0))
        window.subject.setText('项目进展与本周计划')
        window.headers.setText('发件人：产品协作组 <team@example.invalid>\n收件人：demo@example.invalid\n日期：2026-10-16 09:30')
        window.reader.setPlainText('各位同事：\n\n本周的设计评审已经完成。请查看以下工作安排：\n\n1. 完成邮箱工作台的交互优化\n2. 验证草稿保存和邮件阅读流程\n3. 整理下一阶段的功能清单\n\n如有调整，请回复本邮件。\n\n产品协作组\n\n（以上内容为合成演示数据）')
        for dark in (False, True):
            apply_theme(app, dark)
            theme = 'dark' if dark else 'light'
            for width, height in ((1280, 800), (1000, 680)):
                window.resize(width, height)
                app.processEvents()
                window.grab().save(str(output / f'usability-{theme}-{width}.png'))
            window.stack.setCurrentWidget(window.login)
            app.processEvents()
            window.grab().save(str(output / f'usability-login-{theme}.png'))
            window.stack.setCurrentWidget(window.splitter)
        from dataclasses import replace
        from coremail_desktop.compose.service import ComposeService
        from coremail_desktop.models import DraftAttachment
        draft=replace(ComposeService.new_draft('demo@example.invalid'),to=('team@example.invalid',),subject='项目材料 · 合成示例',body='各位同事：\n\n附件为本周项目材料，请查收。\n\n（合成演示数据）',attachments=(DraftAttachment('项目说明.txt','text/plain',b'synthetic preview'),DraftAttachment('测试数据.bin','application/octet-stream',b'\x00\xff')))
        window.open_composer(draft)
        for dark in (False,True):
            apply_theme(app,dark); app.processEvents()
            window.composer.grab().save(str(output / ('usability-composer-'+('dark' if dark else 'light')+'.png')))
        window.composer.close()
        window.close()
        app.quit()
        print('Saved eight synthetic UI snapshots in analysis/')
    QTimer.singleShot(300, capture)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
