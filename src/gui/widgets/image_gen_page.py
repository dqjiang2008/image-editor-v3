"""Image generation page — text-to-image / image-to-image testing."""

import asyncio
import base64
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.config.settings import Settings


class _ImageGenWorker(QThread):
    """Background thread for image generation."""
    image_ready = pyqtSignal(bytes, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, client, prompt, size, n, ref_image=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.prompt = prompt
        self.size = size
        self.n = n
        self.ref_image = ref_image

    def run(self):
        async def _do():
            session = await self.client._get_session()
            data = {
                "model": Settings.IMAGE_MODEL,
                "prompt": self.prompt,
                "n": self.n,
                "size": self.size,
            }
            extra_body = {"response_format": "b64_json"}
            if self.ref_image:
                extra_body["image"] = [self.ref_image]
            data["extra_body"] = extra_body

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.client.api_key}",
            }
            url = f"{self.client.base_url}/v1/images/generations"
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {text[:300]}")
                result = await resp.json()
                items = result.get("data", [])
                if not items:
                    raise Exception("API 未返回图像数据")
                out = []
                for item in items:
                    if "b64_json" in item:
                        img_bytes = base64.b64decode(item["b64_json"])
                        out.append(img_bytes)
                    elif "url" in item:
                        async with session.get(item["url"]) as img_resp:
                            if img_resp.status == 200:
                                out.append(await img_resp.read())
                            else:
                                raise Exception(f"图片下载失败: HTTP {img_resp.status}")
                return out

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            images = loop.run_until_complete(_do())
            for img_bytes in images:
                self.image_ready.emit(img_bytes, self.prompt)
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                loop.close()
            except Exception:
                pass


class _ImageCard(QWidget):
    """A single image result card."""

    def __init__(self, img_bytes, prompt, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        self._img_bytes = img_bytes
        self._pixmap = QPixmap()
        self._pixmap.loadFromData(img_bytes)

        self._label = QLabel()
        self._label.setPixmap(self._pixmap.scaled(
            240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ))
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("border: 1px solid #e2e8f0; border-radius: 6px; background: #f8fafc;")
        layout.addWidget(self._label)

        prompt_label = QLabel(prompt[:60] + ("..." if len(prompt) > 60 else ""))
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet("font-size: 11px; color: #64748b;")
        layout.addWidget(prompt_label)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 保存")
        save_btn.setFixedHeight(26)
        save_btn.setStyleSheet(
            "QPushButton{background:#3b82f6;font-size:11px;} QPushButton:hover{background:#2563eb;}"
        )
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(26, 26)
        del_btn.setStyleSheet(
            "QPushButton{background:#ef4444;color:white;font-size:11px;} QPushButton:hover{background:#dc2626;}"
        )
        del_btn.clicked.connect(self._delete)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", f"agnes_image_{datetime.now().strftime('%H%M%S')}.png",
            "PNG (*.png);;JPEG (*.jpg)"
        )
        if path:
            self._pixmap.save(path)

    def _delete(self):
        parent = self.parent()
        if parent:
            parent_layout = parent.layout()
            if parent_layout:
                parent_layout.removeWidget(self)
        self.deleteLater()


class ImageGenPage(QWidget):
    """Standalone image generation page for testing image model."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._worker = None
        self._cards = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("🎨 AI 画图测试")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        layout.addWidget(header)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._text2img_btn = QPushButton("文生图")
        self._text2img_btn.setCheckable(True)
        self._text2img_btn.setChecked(True)
        self._text2img_btn.setStyleSheet(self._mode_btn_style(True))
        self._text2img_btn.clicked.connect(lambda: self._set_mode("text2img"))
        mode_row.addWidget(self._text2img_btn)

        self._img2img_btn = QPushButton("图生图")
        self._img2img_btn.setCheckable(True)
        self._img2img_btn.setStyleSheet(self._mode_btn_style(False))
        self._img2img_btn.clicked.connect(lambda: self._set_mode("img2img"))
        mode_row.addWidget(self._img2img_btn)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        self._ref_row = QWidget()
        ref_layout = QHBoxLayout(self._ref_row)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.setSpacing(8)
        ref_layout.addWidget(QLabel("参考图:"))
        self._ref_url_input = QLabel("（未选择）")
        self._ref_url_input.setStyleSheet("color: #94a3b8; font-size: 11px;")
        ref_layout.addWidget(self._ref_url_input, 1)
        self._ref_file_btn = QPushButton("📁 选择文件")
        self._ref_file_btn.setFixedHeight(28)
        self._ref_file_btn.setStyleSheet(
            "QPushButton{background:#64748b;font-size:11px;} QPushButton:hover{background:#475569;}"
        )
        self._ref_file_btn.clicked.connect(self._pick_ref_file)
        ref_layout.addWidget(self._ref_file_btn)
        self._ref_preview = QLabel()
        self._ref_preview.setFixedSize(48, 48)
        self._ref_preview.setStyleSheet("border: 1px solid #e2e8f0; border-radius: 4px; background: #f8fafc;")
        self._ref_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ref_layout.addWidget(self._ref_preview)
        self._ref_row.hide()
        layout.addWidget(self._ref_row)

        self._ref_data_uri = None

        self._prompt = QPlainTextEdit()
        self._prompt.setPlaceholderText("描述你想要的图像...")
        self._prompt.setMaximumHeight(80)
        self._prompt.setStyleSheet("""
            QPlainTextEdit {
                border: 2px solid #e2e8f0; border-radius: 8px;
                padding: 8px; font-size: 13px; background: #ffffff;
            }
            QPlainTextEdit:focus { border-color: #22c55e; }
        """)
        layout.addWidget(self._prompt)

        params_row = QHBoxLayout()
        params_row.setSpacing(12)

        params_row.addWidget(QLabel("尺寸:"))
        self._size_combo = QComboBox()
        self._size_combo.addItems(["1024x1024", "1792x1024", "1024x1792"])
        self._size_combo.setFixedWidth(120)
        params_row.addWidget(self._size_combo)

        params_row.addWidget(QLabel("数量:"))
        self._n_spin = QSpinBox()
        self._n_spin.setRange(1, 4)
        self._n_spin.setValue(1)
        params_row.addWidget(self._n_spin)

        self._gen_btn = QPushButton("🎨 生成")
        self._gen_btn.setFixedHeight(36)
        self._gen_btn.setStyleSheet("""
            QPushButton {
                background: #22c55e; color: white; font-weight: bold;
                border-radius: 8px; font-size: 14px;
            }
            QPushButton:hover { background: #16a34a; }
            QPushButton:disabled { background: #cbd5e1; color: #94a3b8; }
        """)
        self._gen_btn.clicked.connect(self._generate)
        params_row.addWidget(self._gen_btn)
        params_row.addStretch()
        layout.addLayout(params_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(self._status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._gallery_widget = QWidget()
        self._gallery_layout = QGridLayout(self._gallery_widget)
        self._gallery_layout.setSpacing(8)
        self._gallery_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self._gallery_widget)
        layout.addWidget(scroll, 1)

    @staticmethod
    def _mode_btn_style(active):
        if active:
            return (
                "QPushButton{background:#22c55e;color:white;font-weight:bold;"
                "border-radius:16px;padding:6px 18px;font-size:12px;}"
            )
        return (
            "QPushButton{background:transparent;color:#64748b;border:1px solid #e2e8f0;"
            "border-radius:16px;padding:6px 18px;font-size:12px;}"
            "QPushButton:hover{border-color:#22c55e;color:#1e293b;}"
        )

    def _set_mode(self, mode):
        is_text = mode == "text2img"
        self._text2img_btn.setChecked(is_text)
        self._img2img_btn.setChecked(not is_text)
        self._text2img_btn.setStyleSheet(self._mode_btn_style(is_text))
        self._img2img_btn.setStyleSheet(self._mode_btn_style(not is_text))
        self._ref_row.setVisible(not is_text)
        self._mode = mode

    def _pick_ref_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择参考图", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        p = Path(path)
        self._ref_url_input.setText(p.name)
        self._ref_url_input.setStyleSheet("color: #1e293b; font-size: 11px;")
        pix = QPixmap(str(p))
        if not pix.isNull():
            self._ref_preview.setPixmap(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        ext = p.suffix.lstrip(".").lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
        self._ref_data_uri = f"data:{mime};base64,{b64}"

    def set_client(self, client):
        self._client = client

    def _generate(self):
        prompt = self._prompt.toPlainText().strip()
        if not prompt:
            self._status_label.setText("⚠️ 请输入描述")
            return
        if not getattr(self, "_client", None) or not self._client.api_key:
            self._status_label.setText("⚠️ 请先配置 API Key")
            return
        if self._busy:
            return

        size = self._size_combo.currentText()
        n = self._n_spin.value()
        ref = None
        if getattr(self, "_mode", "text2img") == "img2img" and self._ref_data_uri:
            ref = self._ref_data_uri

        self._busy = True
        self._gen_btn.setEnabled(False)
        self._status_label.setText("⏳ 正在生成...")

        self._worker = _ImageGenWorker(self._client, prompt, size, n, ref_image=ref)
        self._worker.image_ready.connect(self._on_image_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_image_ready(self, img_bytes, prompt):
        card = _ImageCard(img_bytes, prompt)
        col = len(self._cards) % 3
        row = len(self._cards) // 3
        self._gallery_layout.addWidget(card, row, col)
        self._cards.append(card)
        self._status_label.setText(f"✅ 生成完成，共 {len(self._cards)} 张图")
        self._check_finish()

    def _on_error(self, err):
        self._status_label.setText(f"❌ 错误: {err}")
        self._check_finish()

    def _check_finish(self):
        if self._worker and self._worker.isFinished():
            self._busy = False
            self._gen_btn.setEnabled(True)
            self._worker = None