"""Chat page — direct text conversation with Agnes AI (streaming)."""

import asyncio
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QSpinBox,
)

from src.config.settings import Settings


class _ChatWorker(QThread):
    """Background thread for non-stream chat completion."""
    response_ready = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, client, messages, temperature, max_tokens, parent=None):
        super().__init__(parent)
        self.client = client
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens

    def run(self):
        async def _do():
            session = await self.client._get_session()
            text_config = self.client.text_api_config()
            data = {
                "model": text_config["model"],
                "messages": self.messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {text_config['api_key'] or self.client.api_key}",
            }
            url = f"{(text_config['base_url'] or self.client.base_url).rstrip('/')}/v1/chat/completions"
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {text[:300]}")
                result = await resp.json()
                choice = result.get("choices", [{}])[0]
                content = (choice.get("message") or {}).get("content", "")
                reasoning = (choice.get("message") or {}).get("reasoning_content", "")
                return content, reasoning

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            content, reasoning = loop.run_until_complete(_do())
            self.response_ready.emit(content, reasoning)
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                loop.close()
            except Exception:
                pass


class _StreamWorker(QThread):
    """Background thread for streaming chat completion."""
    chunk_received = pyqtSignal(str, str)
    finished_ok = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, client, messages, temperature, max_tokens, parent=None):
        super().__init__(parent)
        self.client = client
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens

    def run(self):
        async def _do():
            session = await self.client._get_session()
            text_config = self.client.text_api_config()
            data = {
                "model": text_config["model"],
                "messages": self.messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {text_config['api_key'] or self.client.api_key}",
            }
            url = f"{(text_config['base_url'] or self.client.base_url).rstrip('/')}/v1/chat/completions"
            content = ""
            reasoning = ""
            async with session.post(url, headers=headers, json=data) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"HTTP {resp.status}: {text[:300]}")
                buffer = ""
                async for raw_chunk in resp.content.iter_any():
                    buffer += raw_chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            j = json.loads(payload)
                            delta = (j.get("choices") or [{}])[0].get("delta", {})
                            if delta.get("reasoning_content"):
                                reasoning += delta["reasoning_content"]
                            if delta.get("content"):
                                content += delta["content"]
                                self.chunk_received.emit(content, reasoning)
                        except json.JSONDecodeError:
                            continue
            return content, reasoning

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            content, reasoning = loop.run_until_complete(_do())
            self.finished_ok.emit(content, reasoning)
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                loop.close()
            except Exception:
                pass


class ChatPage(QWidget):
    """Standalone chat page for testing text model."""

    CHAT_HISTORY_FILE = Settings.DATA_DIR / "chat_history.json"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []
        self._busy = False
        self._worker = None
        self._build_ui()
        self._load_history()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("💬 AI 对话测试")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        layout.addWidget(header)

        self._chat_display = QTextBrowser()
        self._chat_display.setOpenExternalLinks(True)
        self._chat_display.setStyleSheet("""
            QTextBrowser {
                background-color: #f8fafc;
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
            }
        """)
        self._chat_display.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self._chat_display, 1)

        params_row = QHBoxLayout()
        params_row.setSpacing(12)

        params_row.addWidget(QLabel("温度:"))
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 20)
        self._temp_slider.setValue(7)
        self._temp_slider.setFixedWidth(100)
        params_row.addWidget(self._temp_slider)
        self._temp_label = QLabel("0.7")
        self._temp_label.setFixedWidth(30)
        self._temp_slider.valueChanged.connect(
            lambda v: self._temp_label.setText(f"{v / 10:.1f}")
        )
        params_row.addWidget(self._temp_label)

        params_row.addWidget(QLabel("Max Tokens:"))
        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(256, 32768)
        self._max_tokens_spin.setValue(4096)
        self._max_tokens_spin.setSingleStep(256)
        params_row.addWidget(self._max_tokens_spin)

        self._stream_cb = QCheckBox("流式输出")
        self._stream_cb.setChecked(True)
        params_row.addWidget(self._stream_cb)

        self._clear_btn = QPushButton("🗑️ 清空")
        self._clear_btn.setStyleSheet(
            "QPushButton{background:#ef4444;} QPushButton:hover{background:#dc2626;}"
        )
        self._clear_btn.setFixedHeight(30)
        self._clear_btn.clicked.connect(self._clear_chat)
        params_row.addWidget(self._clear_btn)

        params_row.addStretch()
        layout.addLayout(params_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("输入消息，Enter 发送，Shift+Enter 换行...")
        self._input.setMaximumHeight(80)
        self._input.setStyleSheet("""
            QPlainTextEdit {
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
                background: #ffffff;
            }
            QPlainTextEdit:focus {
                border-color: #3b82f6;
            }
        """)
        self._input.keyPressEvent = self._on_key_press
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedSize(80, 50)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background: #3b82f6; color: white; font-weight: bold;
                border-radius: 8px; font-size: 14px;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:disabled { background: #cbd5e1; color: #94a3b8; }
        """)
        self._send_btn.clicked.connect(self._send_message)
        input_row.addWidget(self._send_btn)

        layout.addLayout(input_row)

    def _on_key_press(self, event):
        if event.key() == Qt.Key.Key_Return and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._send_message()
            return
        from PyQt6.QtGui import QKeyEvent
        QPlainTextEdit.keyPressEvent(self._input, event)

    def set_client(self, client):
        self._client = client

    def _send_message(self):
        text = self._input.toPlainText().strip()
        if not text or self._busy:
            return
        if not getattr(self, "_client", None) or not self._client.api_key:
            self._append_system_msg("⚠️ 请先配置 API Key")
            return

        self._input.clear()
        self._messages.append({"role": "user", "content": text})
        self._render_messages()

        self._busy = True
        self._send_btn.setEnabled(False)

        temperature = self._temp_slider.value() / 10.0
        max_tokens = self._max_tokens_spin.value()

        if self._stream_cb.isChecked():
            self._worker = _StreamWorker(
                self._client, list(self._messages), temperature, max_tokens
            )
            self._worker.chunk_received.connect(self._on_stream_chunk)
            self._worker.finished_ok.connect(self._on_stream_done)
            self._worker.error_occurred.connect(self._on_error)
        else:
            self._worker = _ChatWorker(
                self._client, list(self._messages), temperature, max_tokens
            )
            self._worker.response_ready.connect(self._on_response)
            self._worker.error_occurred.connect(self._on_error)

        self._worker.start()

    def _on_response(self, content, reasoning):
        self._messages.append({"role": "assistant", "content": content, "reasoning": reasoning})
        self._render_messages()
        self._finish_request()

    def _on_stream_chunk(self, content, reasoning):
        self._render_messages(stream_content=content, stream_reasoning=reasoning)

    def _on_stream_done(self, content, reasoning):
        self._messages.append({"role": "assistant", "content": content, "reasoning": reasoning})
        self._render_messages()
        self._finish_request()

    def _on_error(self, err):
        self._messages.append({"role": "assistant", "content": f"❌ 错误: {err}", "is_error": True})
        self._render_messages()
        self._finish_request()

    def _finish_request(self):
        self._busy = False
        self._send_btn.setEnabled(True)
        self._worker = None
        self._save_history()

    def _clear_chat(self):
        self._messages.clear()
        self._chat_display.clear()
        self._save_history()

    def _load_history(self):
        try:
            path = self.CHAT_HISTORY_FILE
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._messages = data
                    self._render_messages()
        except Exception as e:
            print(f"[ChatPage] 加载对话历史失败: {e}", file=sys.stderr, flush=True)

    def _save_history(self):
        try:
            path = self.CHAT_HISTORY_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._messages, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ChatPage] 保存对话历史失败: {e}", file=sys.stderr, flush=True)

    def _append_system_msg(self, msg):
        self._chat_display.append(f'<span style="color:#ef4444;">{msg}</span>')

    def _render_messages(self, stream_content=None, stream_reasoning=None):
        html_parts = []
        for m in self._messages:
            role = m["role"]
            content = m.get("content", "")
            reasoning = m.get("reasoning", "")
            is_error = m.get("is_error", False)

            if role == "user":
                bg = "#eff6ff"
                border = "#3b82f6"
                label = "你"
            else:
                bg = "#f0fdf4" if not is_error else "#fef2f2"
                border = "#22c55e" if not is_error else "#ef4444"
                label = "Agnes"

            content_html = self._simple_markdown(content) if not is_error else self._escape(content)
            reasoning_html = ""
            if reasoning:
                reasoning_html = (
                    f'<details style="margin-bottom:6px;">'
                    f'<summary style="cursor:pointer;color:#7c3aed;font-size:12px;">🧠 推理过程</summary>'
                    f'<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:6px;'
                    f'padding:8px;margin-top:4px;font-size:12px;color:#6b7280;">'
                    f'{self._simple_markdown(reasoning)}</div></details>'
                )

            html_parts.append(
                f'<div style="background:{bg};border-left:3px solid {border};'
                f'padding:8px 12px;margin:4px 0;border-radius:4px;">'
                f'<b style="font-size:11px;color:{border};">{label}</b>'
                f'{reasoning_html}'
                f'<div style="margin-top:4px;">{content_html}</div></div>'
            )

        if stream_content is not None:
            content_html = self._simple_markdown(stream_content)
            reasoning_html = ""
            if stream_reasoning:
                reasoning_html = (
                    f'<details style="margin-bottom:6px;">'
                    f'<summary style="cursor:pointer;color:#7c3aed;font-size:12px;">🧠 推理中...</summary>'
                    f'<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:6px;'
                    f'padding:8px;margin-top:4px;font-size:12px;color:#6b7280;">'
                    f'{self._simple_markdown(stream_reasoning)}</div></details>'
                )
            html_parts.append(
                f'<div style="background:#f0fdf4;border-left:3px solid #22c55e;'
                f'padding:8px 12px;margin:4px 0;border-radius:4px;">'
                f'<b style="font-size:11px;color:#22c55e;">Agnes (流式)</b>'
                f'{reasoning_html}'
                f'<div style="margin-top:4px;">{content_html}</div></div>'
            )

        self._chat_display.setHtml("".join(html_parts))
        self._chat_display.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _escape(s):
        return (
            s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
        )

    @staticmethod
    def _simple_markdown(text):
        if not text:
            return ""
        import re
        h = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        h = re.sub(r"```(\w*)\n([\s\S]*?)```", r"<pre><code>\2</code></pre>", h)
        h = re.sub(r"`([^`]+)`", r"<code>\1</code>", h)
        h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
        h = re.sub(r"\*(.+?)\*", r"<em>\1</em>", h)
        h = re.sub(r"\n", "<br>", h)
        return h
