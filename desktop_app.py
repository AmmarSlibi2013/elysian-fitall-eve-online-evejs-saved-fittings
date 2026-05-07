#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import fitall


APP_TITLE = "Elysian FitALL"
WINDOW_W = 620
WINDOW_H = 760


def format_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.2f}s"


def choose_font(size: int, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    for family in ("SF Pro Display", "Segoe UI Variable Display", "Segoe UI", "Inter", "Arial"):
        if family in QFontDatabase.families():
            return QFont(family, size, weight)
    return QFont("Arial", size, weight)


class AuroraIcon(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(168, 168)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(14, 14, self.width() - 28, self.height() - 28)

        glow = QLinearGradient(rect.topLeft(), rect.bottomRight())
        glow.setColorAt(0.0, QColor(38, 245, 255, 45))
        glow.setColorAt(0.5, QColor(255, 120, 184, 40))
        glow.setColorAt(1.0, QColor(255, 210, 115, 36))
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(rect.adjusted(-8, -8, 8, 8))

        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._phase * 360)
        for index, color in enumerate((QColor("#27f4ff"), QColor("#ff7bb8"), QColor("#ffd372"))):
            painter.save()
            painter.rotate(index * 120)
            pen = QPen(color, 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(-60, -60, 120, 120), 18 * 16, 104 * 16)
            painter.restore()

        painter.rotate(-self._phase * 360)
        painter.setPen(QPen(QColor("#eaf3ff"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor(12, 24, 38, 235))
        badge = QPainterPath()
        badge.addRoundedRect(QRectF(-42, -42, 84, 84), 24, 24)
        painter.drawPath(badge)

        painter.setPen(QPen(QColor("#2af6ff"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(-20, -8), QPointF(0, 14))
        painter.drawLine(QPointF(0, 14), QPointF(26, -20))
        painter.setPen(QPen(QColor("#ff7bb8"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(-26, 28), QPointF(26, 28))


class PulseBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)
        self._progress = 0.0
        self._phase = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(20)

    def set_progress(self, value: float, *, active: bool) -> None:
        self._progress = max(0.0, min(1.0, value))
        self._active = active
        self.update()

    def _tick(self) -> None:
        if self._active:
            self._phase = (self._phase + 0.025) % 1.0
            self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0, 0, self.width(), self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 28))
        painter.drawRoundedRect(track, 7, 7)

        fill = QRectF(0, 0, max(12, self.width() * self._progress), self.height())
        gradient = QLinearGradient(fill.topLeft(), fill.topRight())
        gradient.setColorAt(0.0, QColor("#2af6ff"))
        gradient.setColorAt(0.56, QColor("#ff7bb8"))
        gradient.setColorAt(1.0, QColor("#ffd372"))
        painter.setBrush(gradient)
        painter.drawRoundedRect(fill, 7, 7)

        if self._active and self._progress > 0.03:
            sweep_x = (self._phase * (self.width() + 120)) - 80
            sweep = QLinearGradient(QPointF(sweep_x, 0), QPointF(sweep_x + 80, 0))
            sweep.setColorAt(0.0, QColor(255, 255, 255, 0))
            sweep.setColorAt(0.5, QColor(255, 255, 255, 90))
            sweep.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(sweep)
            painter.drawRoundedRect(fill, 7, 7)


class MetricCard(QFrame):
    def __init__(self, value: str, label: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setStyleSheet(
            f"""
            QFrame#MetricCard {{
                border: 1px solid {accent};
                border-radius: 18px;
                background: rgba(11, 24, 39, 0.72);
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setFont(choose_font(24, QFont.Weight.Black))
        self.value_label.setStyleSheet(f"color: {accent};")
        self.caption = QLabel(label)
        self.caption.setFont(choose_font(9, QFont.Weight.Bold))
        self.caption.setStyleSheet("color: rgba(235,244,255,0.78);")
        self.caption.setWordWrap(True)
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption)

    def set_metric(self, value: str, label: str | None = None) -> None:
        self.value_label.setText(value)
        if label is not None:
            self.caption.setText(label)


class StagePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(42, 34, 42, 34)
        self.root.setSpacing(22)


class FitWorker(QThread):
    event = Signal(dict)
    finished_ok = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, command: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.command = command

    def run(self) -> None:
        started = time.perf_counter()
        try:
            result = fitall.run_fitall_command(
                command=self.command,
                event_callback=self.event.emit,
                log_callback=None,
            )
            result["elapsedSeconds"] = time.perf_counter() - started
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), traceback.format_exc())


class FitALLWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self._worker: FitWorker | None = None
        self._seed_total = 0
        self._build_total = 0
        self._active_command = "seed-saved-fittings"
        self._last_result: dict[str, Any] = {}

        shell = QWidget()
        shell.setObjectName("Shell")
        shell.setStyleSheet(
            """
            QWidget#Shell {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #07111d, stop:0.48 #111827, stop:1 #1b0b19);
                color: #eef6ff;
            }
            QLabel { color: #eef6ff; }
            QPushButton {
                min-height: 50px;
                border: 0;
                border-radius: 18px;
                padding: 0 22px;
                color: #04111d;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2af6ff, stop:0.55 #ff7bb8, stop:1 #ffd372);
                font-weight: 900;
            }
            QPushButton:hover { background: #f3fbff; }
            QPushButton:disabled {
                color: rgba(255,255,255,0.46);
                background: rgba(255,255,255,0.12);
            }
            QPushButton#Ghost {
                min-height: 42px;
                color: rgba(235,244,255,0.84);
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.13);
            }
            QPushButton#Ghost:hover {
                color: #eef6ff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(42,246,255,0.20), stop:0.55 rgba(255,123,184,0.16), stop:1 rgba(255,211,114,0.18));
                border: 1px solid rgba(42,246,255,0.68);
            }
            QPushButton#RefreshButton {
                min-height: 46px;
                color: #06111d;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #78e5ff, stop:0.48 #8fffd2, stop:1 #ffd372);
                border: 1px solid rgba(255,255,255,0.24);
            }
            QPushButton#RefreshButton:hover {
                color: #020911;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffffff, stop:0.42 #8fffd2, stop:1 #ff7bb8);
                border: 1px solid rgba(255,255,255,0.72);
            }
            QPushButton#Ghost:pressed,
            QPushButton#RefreshButton:pressed {
                background: rgba(255,255,255,0.16);
                padding-top: 2px;
            }
            """
        )
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(16, 16, 16, 16)

        self.panel = QFrame()
        self.panel.setObjectName("Panel")
        self.panel.setStyleSheet(
            """
            QFrame#Panel {
                border: 1px solid rgba(130, 181, 255, 0.26);
                border-radius: 30px;
                background: rgba(8, 18, 31, 0.78);
            }
            """
        )
        outer.addWidget(self.panel)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        panel_layout.addWidget(self.stack)
        self.setCentralWidget(shell)

        self._build_intro_page()
        self._build_ready_page()
        self._build_running_page()
        self._build_done_page()
        self._build_error_page()

        self.stack.setCurrentWidget(self.intro_page)
        self._animate_intro()

    def _headline(self, text: str, size: int = 34) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(choose_font(size, QFont.Weight.Black))
        label.setWordWrap(True)
        return label

    def _body(self, text: str, size: int = 12) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(choose_font(size, QFont.Weight.Medium))
        label.setWordWrap(True)
        label.setStyleSheet("color: rgba(235,244,255,0.72); line-height: 130%;")
        return label

    def _build_intro_page(self) -> None:
        self.intro_page = StagePage()
        self.intro_page.root.addStretch(1)
        self.intro_icon = AuroraIcon()
        self.intro_page.root.addWidget(self.intro_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.intro_title = QLabel("JOHN ELYSIAN")
        self.intro_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.intro_title.setFont(choose_font(24, QFont.Weight.Black))
        self.intro_title.setStyleSheet("color: #2af6ff; letter-spacing: 0px;")
        self.intro_caption = QLabel("PRESENTS...")
        self.intro_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.intro_caption.setFont(choose_font(14, QFont.Weight.Black))
        self.intro_caption.setStyleSheet("color: rgba(255,255,255,0.72);")
        self.intro_page.root.addWidget(self.intro_title)
        self.intro_page.root.addWidget(self.intro_caption)
        self.intro_page.root.addStretch(1)
        self.stack.addWidget(self.intro_page)

        self.intro_opacity = QGraphicsOpacityEffect(self.intro_page)
        self.intro_page.setGraphicsEffect(self.intro_opacity)
        self.intro_opacity.setOpacity(0.0)

    def _animate_intro(self) -> None:
        self._intro_ticks = 0
        self._intro_timer = QTimer(self)
        self._intro_timer.timeout.connect(self._intro_tick)
        self._intro_timer.start(32)

    def _intro_tick(self) -> None:
        self._intro_ticks += 1
        eased = QEasingCurve(QEasingCurve.Type.OutCubic).valueForProgress(min(1.0, self._intro_ticks / 28))
        self.intro_opacity.setOpacity(eased)
        if self._intro_ticks >= 62:
            self._intro_timer.stop()
            self.stack.setCurrentWidget(self.ready_page)
            self.refresh_ready()

    def skip_intro(self) -> None:
        if hasattr(self, "_intro_timer"):
            self._intro_timer.stop()
        self.stack.setCurrentWidget(self.ready_page)
        self.refresh_ready()

    def _build_ready_page(self) -> None:
        self.ready_page = StagePage()
        self.ready_page.root.addStretch(1)
        self.ready_icon = AuroraIcon()
        self.ready_page.root.addWidget(self.ready_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.ready_page.root.addWidget(self._headline("Elysian FitALL", 36))
        self.ready_page.root.addWidget(
            self._body("Adds a saved fitting for every single ship to all characters.", 14)
        )

        metric_row = QHBoxLayout()
        metric_row.setSpacing(12)
        self.ship_metric = MetricCard("415", "ship fittings", "#2af6ff")
        self.coverage_metric = MetricCard("100%", "hull coverage", "#ff7bb8")
        self.click_metric = MetricCard("1", "click seed", "#ffd372")
        metric_row.addWidget(self.ship_metric)
        metric_row.addWidget(self.coverage_metric)
        metric_row.addWidget(self.click_metric)
        self.ready_page.root.addLayout(metric_row)

        self.path_label = self._body("", 10)
        self.path_label.setStyleSheet("color: rgba(235,244,255,0.58);")
        self.ready_page.root.addWidget(self.path_label)

        self.primary_button = QPushButton("Fit Every Character")
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_button.clicked.connect(self.start_seed)
        self.choose_button = QPushButton("Choose EVE JS Folder")
        self.choose_button.setObjectName("Ghost")
        self.choose_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_button.clicked.connect(self.choose_evejs_folder)
        self.refresh_button = QPushButton("Refresh fittings from ESI && Killboards")
        self.refresh_button.setObjectName("RefreshButton")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.start_refresh)
        self.ready_page.root.addWidget(self.primary_button)
        self.ready_page.root.addWidget(self.refresh_button)
        self.ready_page.root.addWidget(self.choose_button)
        self.ready_page.root.addStretch(1)
        self.stack.addWidget(self.ready_page)

    def _build_running_page(self) -> None:
        self.running_page = StagePage()
        self.running_page.root.addStretch(1)
        self.running_icon = AuroraIcon()
        self.running_page.root.addWidget(self.running_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.running_title = self._headline("Fitting every character", 30)
        self.running_page.root.addWidget(self.running_title)
        self.running_body = self._body("Writing the FitALL library into EVE JS saved fittings.", 12)
        self.running_page.root.addWidget(self.running_body)
        self.progress = PulseBar()
        self.running_page.root.addWidget(self.progress)
        self.progress_detail = self._body("Preparing...", 10)
        self.running_page.root.addWidget(self.progress_detail)
        self.running_page.root.addStretch(1)
        self.stack.addWidget(self.running_page)

    def _build_done_page(self) -> None:
        self.done_page = StagePage()
        self.done_page.root.addStretch(1)
        self.done_icon = QLabel("DONE")
        self.done_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_icon.setFont(choose_font(44, QFont.Weight.Black))
        self.done_icon.setStyleSheet("color: #2af6ff;")
        self.done_page.root.addWidget(self.done_icon)
        self.done_title = self._headline("Every character is fitted", 30)
        self.done_page.root.addWidget(self.done_title)
        self.done_body = self._body("", 12)
        self.done_page.root.addWidget(self.done_body)
        done_row = QHBoxLayout()
        done_row.setSpacing(12)
        self.done_chars = MetricCard("0", "characters", "#2af6ff")
        self.done_fits = MetricCard("0", "fits each", "#ff7bb8")
        self.done_time = MetricCard("0ms", "local run", "#ffd372")
        done_row.addWidget(self.done_chars)
        done_row.addWidget(self.done_fits)
        done_row.addWidget(self.done_time)
        self.done_page.root.addLayout(done_row)
        self.done_again = QPushButton("Run Again")
        self.done_again.clicked.connect(self.start_seed)
        self.done_page.root.addWidget(self.done_again)
        self.done_page.root.addStretch(1)
        self.stack.addWidget(self.done_page)

    def _build_error_page(self) -> None:
        self.error_page = StagePage()
        self.error_page.root.addStretch(1)
        self.error_page.root.addWidget(self._headline("FitALL needs one thing", 30))
        self.error_body = self._body("", 12)
        self.error_page.root.addWidget(self.error_body)
        self.error_choose = QPushButton("Choose EVE JS Folder")
        self.error_choose.clicked.connect(self.choose_evejs_folder)
        self.error_retry = QPushButton("Try Again")
        self.error_retry.setObjectName("Ghost")
        self.error_retry.clicked.connect(self.start_seed)
        self.error_page.root.addWidget(self.error_choose)
        self.error_page.root.addWidget(self.error_retry)
        self.error_page.root.addStretch(1)
        self.stack.addWidget(self.error_page)

    def refresh_ready(self) -> None:
        snapshot = fitall.load_tool_snapshot()
        library_summary = snapshot.get("librarySummary") or {}
        ship_list_summary = snapshot.get("shipListSummary") or {}
        ship_count = int(library_summary.get("shipCount") or ship_list_summary.get("shipCount") or 0)
        harvested = int(library_summary.get("harvestedCount") or 0)
        self.ship_metric.set_metric(format_count(harvested or ship_count))
        coverage = "100%" if ship_count and harvested >= ship_count else f"{harvested}/{ship_count}"
        self.coverage_metric.set_metric(coverage if harvested else "refresh")

        if fitall.looks_like_evejs_root(fitall.REPO_ROOT):
            self.path_label.setText(f"EVE JS ready: {fitall.REPO_ROOT}")
            self.primary_button.setEnabled(bool(harvested))
            self.refresh_button.setEnabled(True)
            self.primary_button.setText("Fit Every Character" if harvested else "Refresh Fittings First")
        else:
            self.path_label.setText("Choose your EVE JS folder once. FitALL remembers it after setup.")
            self.primary_button.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.primary_button.setText("Choose EVE JS Folder")

    @Slot()
    def choose_evejs_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose your EVE JS folder", str(Path.home()))
        if not folder:
            return
        try:
            fitall.configure_evejs_root(Path(folder), persist=True)
            if not fitall.looks_like_evejs_root(fitall.REPO_ROOT):
                raise FileNotFoundError("That folder does not look like an EVE JS checkout.")
            self.stack.setCurrentWidget(self.ready_page)
            self.refresh_ready()
        except Exception as exc:  # noqa: BLE001
            self.show_error(str(exc))

    @Slot()
    def start_seed(self) -> None:
        if not fitall.looks_like_evejs_root(fitall.REPO_ROOT):
            self.choose_evejs_folder()
            return
        if not (fitall.load_library_payload(required=False).get("records") or []):
            self.show_error(
                "No local fitting library was found yet. Click Refresh fittings from ESI & Killboards "
                "to rebuild it from the embedded ship catalog."
            )
            self.error_retry.setText("Refresh fittings from ESI & Killboards")
            try:
                self.error_retry.clicked.disconnect()
            except RuntimeError:
                pass
            self.error_retry.clicked.connect(self.start_refresh)
            return
        if self._worker and self._worker.isRunning():
            return
        self._active_command = "seed-saved-fittings"
        self._seed_total = 0
        self.running_title.setText("Fitting every character")
        self.running_body.setText("Writing the FitALL library into EVE JS saved fittings.")
        self.progress.set_progress(0.03, active=True)
        self.progress_detail.setText("Loading characters...")
        self.stack.setCurrentWidget(self.running_page)
        self._worker = FitWorker("seed-saved-fittings", self)
        self._worker.event.connect(self.on_worker_event)
        self._worker.finished_ok.connect(self.on_worker_done)
        self._worker.failed.connect(self.on_worker_failed)
        self._worker.start()

    @Slot()
    def start_refresh(self) -> None:
        if not fitall.looks_like_evejs_root(fitall.REPO_ROOT):
            self.choose_evejs_folder()
            return
        if self._worker and self._worker.isRunning():
            return
        self._active_command = "build-library"
        self._build_total = 0
        self.running_title.setText("Refreshing fittings")
        self.running_body.setText("Downloading fresh public fits from ESI and zKillboard.")
        self.progress.set_progress(0.02, active=True)
        self.progress_detail.setText("Preparing ship list...")
        self.stack.setCurrentWidget(self.running_page)
        self._worker = FitWorker("build-library", self)
        self._worker.event.connect(self.on_worker_event)
        self._worker.finished_ok.connect(self.on_worker_done)
        self._worker.failed.connect(self.on_worker_failed)
        self._worker.start()

    @Slot(dict)
    def on_worker_event(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind")
        if kind == "seed-start":
            self._seed_total = int(payload.get("totalCharacters") or 0)
            self.running_body.setText(
                f"{format_count(payload.get('fitsPerCharacter'))} ship fittings per character."
            )
            self.progress_detail.setText(f"Preparing {format_count(self._seed_total)} characters...")
            self.progress.set_progress(0.08, active=True)
            return
        if kind == "seed-progress":
            current = int(payload.get("current") or 0)
            total = max(1, int(payload.get("total") or self._seed_total or 1))
            character_name = str(payload.get("characterName") or f"Character {payload.get('characterID') or ''}")
            self.progress.set_progress(current / total, active=True)
            self.progress_detail.setText(f"{current}/{total} - {character_name}")
            return
        if kind == "seed-complete":
            self.progress.set_progress(1.0, active=False)
            self.progress_detail.setText("Saved fittings written.")
            return
        if kind == "build-start":
            self._build_total = int(payload.get("total") or 0)
            self.running_body.setText("Downloading fresh public fits from ESI and zKillboard.")
            self.progress_detail.setText(f"Preparing {format_count(self._build_total)} ships...")
            self.progress.set_progress(0.04, active=True)
            return
        if kind == "build-progress":
            current = int(payload.get("current") or 0)
            total = max(1, int(payload.get("total") or self._build_total or 1))
            ship_name = str(payload.get("shipName") or "ship")
            status = "ready" if payload.get("status") == "ok" else "searching"
            self.progress.set_progress(current / total, active=True)
            self.progress_detail.setText(f"{current}/{total} - {ship_name} - {status}")
            return
        if kind == "build-complete":
            summary = payload.get("summary") or {}
            self.progress.set_progress(1.0, active=False)
            self.progress_detail.setText(
                f"Refreshed {format_count(summary.get('harvestedCount'))} / "
                f"{format_count(summary.get('shipCount'))} fittings."
            )

    @Slot(dict)
    def on_worker_done(self, result: dict[str, Any]) -> None:
        self._worker = None
        self._last_result = result
        elapsed = float(result.get("elapsedSeconds") or 0)
        if result.get("command") == "build-library":
            summary = result.get("librarySummary") or {}
            self.done_title.setText("Fitting library refreshed")
            self.done_chars.set_metric(format_count(summary.get("harvestedCount")), "ship fittings")
            self.done_fits.set_metric(format_count(summary.get("publicCount")), "public ESI fits")
            self.done_time.set_metric(format_seconds(elapsed), "refresh run")
            self.done_body.setText(
                "Fresh fittings are now saved locally. The main button will seed this refreshed library."
            )
            self.done_again.setText("Fit Every Character")
            self.done_again.clicked.disconnect()
            self.done_again.clicked.connect(self.start_seed)
        else:
            summary = result.get("seedSummary") or {}
            self.done_title.setText("Every character is fitted")
            self.done_chars.set_metric(format_count(summary.get("charactersSeeded")), "characters")
            self.done_fits.set_metric(format_count(summary.get("toolLibraryFits")), "fits each")
            self.done_time.set_metric(format_seconds(elapsed), "local run")
            self.done_body.setText(
                f"{format_count(summary.get('toolRecordsAdded'))} FitALL rows written into EVE JS saved fittings."
            )
            self.done_again.setText("Run Again")
            self.done_again.clicked.disconnect()
            self.done_again.clicked.connect(self.start_seed)
        self.refresh_ready()
        self.stack.setCurrentWidget(self.done_page)

    @Slot(str, str)
    def on_worker_failed(self, message: str, traceback_text: str) -> None:
        self._worker = None
        error_log = fitall.TOOL_ROOT / "last-crash.log"
        error_log.write_text(traceback_text, encoding="utf-8")
        self.show_error(message)

    def show_error(self, message: str) -> None:
        self.error_retry.setText("Try Again")
        try:
            self.error_retry.clicked.disconnect()
        except RuntimeError:
            pass
        self.error_retry.clicked.connect(self.start_seed)
        self.error_body.setText(message)
        self.stack.setCurrentWidget(self.error_page)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the Elysian FitALL desktop app.")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--no-show", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("John Elysian")
    app.setFont(choose_font(10, QFont.Weight.Medium))

    window = FitALLWindow()
    if args.screenshot:
        window.skip_intro()
    if not args.no_show:
        window.show()

    if args.screenshot:
        def grab() -> None:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
            app.quit()

        QTimer.singleShot(500, grab)
    elif args.smoke_test:
        QTimer.singleShot(450, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
