"""剪映自动化控制，主要与自动导出有关"""

import os
import time
import shutil
import uiautomation as uia

from enum import Enum
from typing import Optional, Literal, Callable, List

from . import exceptions
from .exceptions import AutomationError

class ExportResolution(Enum):
    """导出分辨率"""
    RES_8K = "8K"
    RES_4K = "4K"
    RES_2K = "2K"
    RES_1080P = "1080P"
    RES_720P = "720P"
    RES_480P = "480P"

class ExportFramerate(Enum):
    """导出帧率"""
    FR_24 = "24fps"
    FR_25 = "25fps"
    FR_30 = "30fps"
    FR_50 = "50fps"
    FR_60 = "60fps"

class ControlFinder:
    """控件查找器，封装部分与控件查找相关的逻辑"""

    @staticmethod
    def desc_matcher(target_desc: str, depth: int = 2, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据full_description查找控件的匹配器"""
        target_desc = target_desc.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            full_desc: str = control.GetPropertyValue(30159).lower()
            return (target_desc == full_desc) if exact else (target_desc in full_desc)
        return matcher

    @staticmethod
    def class_name_matcher(class_name: str, depth: int = 1, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据ClassName查找控件的匹配器"""
        class_name = class_name.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            curr_class_name: str = control.ClassName.lower()
            return (class_name == curr_class_name) if exact else (class_name in curr_class_name)
        return matcher

class JianyingController:
    """剪映控制器"""

    app: uia.WindowControl
    """剪映窗口"""
    app_status: Literal["home", "edit", "pre_export"]

    # 仅匹配明确的干扰弹窗标题/文案（禁止对剪映主窗做深度 SubName 扫描，否则会卡死）
    _BLOCKING_POPUP_KEYWORDS = (
        "未检测到音频",
        "未检测到",
        "音频设备",
        "没有监测的音频",
        "监测的音频",
        "未发现音频",
    )
    # 「草稿列表异常」必须点「取消」（点确认会进恢复引导，挡住目录/编辑）
    _DRAFT_LIST_EXCEPTION_KEYWORDS = (
        "草稿列表异常",
        "检测到部分草稿丢失",
        "部分草稿丢失",
        "如何恢复草稿",
        "恢复草稿",
        "草稿丢失",
        "草稿已丢失",
        "无法找到草稿",
        "找不到草稿",
    )
    _DISMISS_BUTTON_NAMES = (
        "确定",
        "确认",
        "知道了",
        "我知道了",
        "好的",
        "OK",
        "Ok",
        "ok",
    )
    _DRAFT_LIST_CANCEL_BUTTON_NAMES = (
        "取消",
        "关闭",
        "否",
    )

    def __init__(self):
        """初始化剪映控制器, 此时剪映应该处于目录页"""
        self.get_window()
        self.dismiss_blocking_dialogs()

    @staticmethod
    def _is_likely_jianying_top(name: str, cls: str) -> bool:
        n = name or ""
        c = (cls or "").lower()
        if any(
            x in c
            for x in ("chrome", "electron", "markdown", "cursor", "monaco", "msedge", "explorer")
        ):
            return False
        if n == "剪映专业版" or n.startswith("剪映专业版"):
            return True
        if "剪映" in n:
            return True
        # 剪映特有类名；勿把任意 Qt MainWindow 当成剪映
        if "homepage" in c or "lvinfodialog" in c:
            return True
        if "mainwindow" in c and ("jianying" in c or "lv" in c):
            return True
        return False

    @classmethod
    def _is_draft_list_exception(cls, text: str) -> bool:
        blob = text or ""
        if "草稿列表异常" in blob:
            return True
        if "检测到部分草稿丢失" in blob:
            return True
        if "部分草稿" in blob and "恢复" in blob:
            return True
        if "草稿丢失" in blob or "草稿已丢失" in blob:
            return True
        # 不再把笼统 LVInfoDialog/JianyingPro 当异常文案（需配合尺寸+命名按钮）
        return any(k in blob for k in ("无法找到草稿", "找不到草稿"))

    @staticmethod
    def _dialog_rect_ok(control) -> bool:
        try:
            rect = control.BoundingRectangle
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            cx = (int(rect.left) + int(rect.right)) // 2
            cy = (int(rect.top) + int(rect.bottom)) // 2
        except Exception:
            return False
        if cx < 0 or cy < 0:
            return False
        return 280 <= w <= 420 and 130 <= h <= 280


    def dismiss_blocking_dialogs(self, rounds: int = 1) -> int:
        """轻量关闭干扰弹窗（音频设备 / 草稿列表异常等）。

        「草稿列表异常」只点「取消」；其它提示点「确定/确认」。
        """
        closed = 0
        for _ in range(max(1, rounds)):
            hit = False
            # 优先：草稿列表异常 → 取消
            try:
                if self._dismiss_draft_list_exception():
                    closed += 1
                    hit = True
                    time.sleep(0.2)
            except Exception:
                pass
            try:
                desktop = uia.GetRootControl()
                for win in desktop.GetChildren():
                    try:
                        name = (win.Name or "")
                        cls = (win.ClassName or "")
                    except Exception:
                        continue
                    if not name:
                        continue
                    if not self._is_likely_jianying_top(name, cls):
                        continue
                    if self._is_draft_list_exception(name):
                        if self._click_named_buttons_under(win, self._DRAFT_LIST_CANCEL_BUTTON_NAMES):
                            closed += 1
                            hit = True
                            print(f"已关闭剪映「草稿列表异常」（取消）: {name}")
                            time.sleep(0.15)
                        continue
                    if not any(k in name for k in self._BLOCKING_POPUP_KEYWORDS):
                        continue
                    if self._click_named_buttons_under(win, self._DISMISS_BUTTON_NAMES):
                        closed += 1
                        hit = True
                        print(f"已关闭剪映提示弹窗: {name}")
                        time.sleep(0.15)
            except Exception:
                pass
            # 主窗内嵌弹窗
            try:
                app = getattr(self, "app", None)
                if app is not None and app.Exists(0):
                    if self._dismiss_by_message_under(app):
                        closed += 1
                        hit = True
                        time.sleep(0.15)
            except Exception:
                pass
            if not hit:
                break
        return closed

    def _dismiss_draft_list_exception(self) -> bool:
        """专门关掉「草稿列表异常」：只点取消。

        实测 UIA：HomePage 下子控件 Name=JianyingPro、ClassName 含 LVInfoDialog，约 353x143。
        """
        try:
            desktop = uia.GetRootControl()
            for win in desktop.GetChildren() or []:
                try:
                    name = win.Name or ""
                    cls = win.ClassName or ""
                except Exception:
                    name, cls = "", ""
                if not self._is_likely_jianying_top(name, cls):
                    continue
                extra = ""
                try:
                    kids = list(win.GetChildren() or [])[:40]
                    extra = " ".join((c.Name or "") for c in kids if getattr(c, "Name", None))
                    # 直接找 LVInfoDialog / JianyingPro 子控件
                    for c in kids:
                        try:
                            cn = c.Name or ""
                            cc = c.ClassName or ""
                        except Exception:
                            continue
                        if "LVInfoDialog" in cc or (
                            cn == "JianyingPro" and "LVInfo" in cc
                        ):
                            if not self._dialog_rect_ok(c):
                                continue
                            if self._click_named_buttons_under(
                                c, self._DRAFT_LIST_CANCEL_BUTTON_NAMES
                            ):
                                print(
                                    f"已关闭剪映「草稿列表异常」（LVInfoDialog）: {cn}/{cc[:40]}"
                                )
                                return True
                            # 无命名按钮则跳过，禁止坐标盲点
                            continue
                except Exception:
                    extra = ""
                if not self._is_draft_list_exception(f"{name} {cls} {extra}"):
                    continue
                if not self._dialog_rect_ok(win) and "LVInfo" not in (cls or ""):
                    # 顶层剪映主窗本身很大，只认子树已在上面处理；此处勿对主窗乱点
                    continue
                if self._click_named_buttons_under(win, self._DRAFT_LIST_CANCEL_BUTTON_NAMES):
                    print(f"已关闭剪映「草稿列表异常」（取消）: {(name or extra)[:40]}")
                    return True
                if self._click_dialog_cancel_by_rect(win):
                    print("已关闭剪映「草稿列表异常」（坐标点取消）")
                    return True
        except Exception:
            pass
        try:
            app = getattr(self, "app", None)
            if app is not None and app.Exists(0):
                if self._dismiss_draft_list_under(app):
                    return True
        except Exception:
            pass
        return False

    def _dismiss_draft_list_under(self, root) -> bool:
        if root is None:
            return False
        try:
            children = list(root.GetChildren() or [])
        except Exception:
            return False
        for child in children:
            try:
                name = child.Name or ""
            except Exception:
                name = ""
            extra = ""
            try:
                extra = " ".join(
                    (g.Name or "") for g in (child.GetChildren() or [])[:24]
                    if getattr(g, "Name", None)
                )
            except Exception:
                extra = ""
            blob = f"{name} {extra}"
            if not self._is_draft_list_exception(blob):
                # 再下一层
                try:
                    for grand in child.GetChildren() or []:
                        try:
                            gname = grand.Name or ""
                        except Exception:
                            continue
                        if self._is_draft_list_exception(gname):
                            if self._click_named_buttons_under(
                                child, self._DRAFT_LIST_CANCEL_BUTTON_NAMES
                            ) or self._click_named_buttons_under(
                                grand, self._DRAFT_LIST_CANCEL_BUTTON_NAMES
                            ):
                                print(f"已关闭剪映「草稿列表异常」（嵌套取消）: {gname[:40]}")
                                return True
                except Exception:
                    pass
                continue
            if self._click_named_buttons_under(child, self._DRAFT_LIST_CANCEL_BUTTON_NAMES):
                print(f"已关闭剪映「草稿列表异常」（取消）: {(name or extra)[:40]}")
                return True
            if self._click_dialog_cancel_by_rect(child):
                print("已关闭剪映「草稿列表异常」（坐标点取消）")
                return True
        return False

    def _click_dialog_cancel_by_rect(self, root) -> bool:
        """【已禁用】禁止用屏幕坐标盲点「取消」。

        历史事故：SetCursorPos 在焦点失败时会点到浏览器关闭钮、开始菜单/设置。
        请只用命名按钮 UIA Click。
        """
        return False

    def _dismiss_by_message_under(self, root) -> bool:
        """在根节点下浅搜含关键词的文案，再点对应按钮。"""
        if root is None:
            return False
        # 草稿列表异常优先
        if self._dismiss_draft_list_under(root):
            return True
        try:
            children = list(root.GetChildren())
        except Exception:
            return False
        for child in children:
            try:
                name = child.Name or ""
            except Exception:
                continue
            if name and any(k in name for k in self._BLOCKING_POPUP_KEYWORDS):
                if self._click_named_buttons_under(child, self._DISMISS_BUTTON_NAMES):
                    print(f"已关闭剪映提示弹窗(文案): {name[:40]}")
                    return True
                parent = None
                try:
                    parent = child.GetParentControl()
                except Exception:
                    parent = None
                if parent is not None and self._click_named_buttons_under(
                    parent, self._DISMISS_BUTTON_NAMES
                ):
                    print(f"已关闭剪映提示弹窗(父级): {name[:40]}")
                    return True
            try:
                for grand in child.GetChildren():
                    try:
                        gname = grand.Name or ""
                    except Exception:
                        continue
                    if not gname or not any(k in gname for k in self._BLOCKING_POPUP_KEYWORDS):
                        continue
                    if self._click_named_buttons_under(
                        child, self._DISMISS_BUTTON_NAMES
                    ) or self._click_named_buttons_under(
                        grand, self._DISMISS_BUTTON_NAMES
                    ):
                        print(f"已关闭剪映提示弹窗(嵌套): {gname[:40]}")
                        return True
            except Exception:
                continue
        return False

    def _click_named_buttons_under(self, root, names) -> bool:
        """仅在已锁定的弹窗根节点下浅搜指定按钮（Exists(0) 避免数秒空等）。"""
        if root is None:
            return False
        for btn_name in names:
            try:
                btn = root.ButtonControl(Name=btn_name, searchDepth=4)
                if btn.Exists(0):
                    btn.Click(simulateMove=False)
                    return True
            except Exception:
                pass
            try:
                txt = root.TextControl(Name=btn_name, searchDepth=4)
                if txt.Exists(0):
                    try:
                        txt.Click(simulateMove=False)
                        return True
                    except Exception:
                        parent = txt.GetParentControl()
                        if parent is not None:
                            parent.Click(simulateMove=False)
                            return True
            except Exception:
                pass
        return False

    def _click_dismiss_button_under(self, root) -> bool:
        """兼容旧调用：默认点确定/确认类按钮。"""
        return self._click_named_buttons_under(root, self._DISMISS_BUTTON_NAMES)
    def _wait_control(self, factory, timeout: float = 15.0, interval: float = 0.25):
        """轮询等待控件出现，返回控件或 None。"""
        deadline = time.time() + timeout
        last_dismiss = 0.0
        while time.time() < deadline:
            now = time.time()
            if now - last_dismiss >= 6.0:
                self.dismiss_blocking_dialogs(rounds=1)
                last_dismiss = now
            self.get_window(activate=False)
            ctrl = factory()
            if ctrl is not None and ctrl.Exists(0):
                return ctrl
            time.sleep(interval)
        return None

    def _read_export_path(self) -> Optional[str]:
        """尝试读取导出对话框中的路径；5.9 部分界面无该自动化节点时返回 None。"""
        for depth in (2, 1):
            try:
                sib = self.app.TextControl(
                    searchDepth=depth,
                    Compare=ControlFinder.desc_matcher("ExportPath"),
                )
                if not sib.Exists(0):
                    continue
                text_ctrl = sib.GetSiblingControl(lambda ctrl: True)
                if text_ctrl is None:
                    continue
                try:
                    path = text_ctrl.GetPropertyValue(30159)
                except Exception:
                    path = getattr(text_ctrl, "Name", None) or ""
                path = (path or "").strip()
                if path:
                    return path
            except Exception:
                continue
        return None

    def _try_set_export_resolution_framerate(
        self,
        resolution: Optional[ExportResolution],
        framerate: Optional[ExportFramerate],
    ) -> None:
        """在导出对话框中尽量设置分辨率/帧率；失败只打印警告，不抛错。"""
        if resolution is None and framerate is None:
            return
        try:
            self.get_window(activate=False)
        except Exception:
            pass

        def _pick_dropdown(input_desc: str, value: str, label: str) -> bool:
            try:
                btn = None
                try:
                    setting_group = self.app.GroupControl(searchDepth=1, foundIndex=4)
                    if setting_group.Exists(0):
                        btn = setting_group.TextControl(
                            searchDepth=2,
                            Compare=ControlFinder.desc_matcher(input_desc),
                        )
                except Exception:
                    btn = None
                if btn is None or not btn.Exists(0):
                    btn = self.app.TextControl(
                        searchDepth=2,
                        Compare=ControlFinder.desc_matcher(input_desc),
                    )
                if not btn.Exists(0.4):
                    print(f"警告: 未找到导出{label}下拉框，跳过设置 {value}")
                    return False
                btn.Click(simulateMove=False)
                time.sleep(0.35)
                item = self.app.TextControl(
                    searchDepth=2,
                    Compare=ControlFinder.desc_matcher(value),
                )
                if not item.Exists(0.5):
                    print(f"警告: 未找到导出{label}选项 {value}，跳过")
                    return False
                item.Click(simulateMove=False)
                time.sleep(0.25)
                print(f"[导出] 已设置{label}: {value}")
                return True
            except Exception as exc:
                print(f"警告: 设置导出{label}失败({value}): {exc}")
                return False

        if resolution is not None:
            _pick_dropdown("ExportSharpnessInput", resolution.value, "分辨率")
        if framerate is not None:
            _pick_dropdown("FrameRateInput", framerate.value, "帧率")

    def _settle_exported_file(
        self,
        *,
        export_path: Optional[str],
        output_path: str,
        draft_name: str,
        since_ts: float,
        timeout: float = 60.0,
    ) -> None:
        """将剪映导出结果落到 output_path（move / 目录轮询）。"""
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if export_path:
            export_path = os.path.abspath(export_path)
            if os.path.isdir(export_path):
                candidates = []
                for name in os.listdir(export_path):
                    if not name.lower().endswith(".mp4"):
                        continue
                    full = os.path.join(export_path, name)
                    try:
                        if os.path.getmtime(full) >= since_ts - 5 and os.path.getsize(full) > 1024:
                            candidates.append(full)
                    except OSError:
                        continue
                if candidates:
                    export_path = max(candidates, key=os.path.getmtime)

            if os.path.isfile(export_path) and os.path.getsize(export_path) > 1024:
                if os.path.abspath(export_path) != output_path:
                    if os.path.isfile(output_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                    shutil.move(export_path, output_path)
                return

        search_dir = os.path.dirname(output_path) or "."
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates = []
            try:
                for name in os.listdir(search_dir):
                    if not name.lower().endswith(".mp4"):
                        continue
                    full = os.path.join(search_dir, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if st.st_mtime < since_ts - 5 or st.st_size < 1024:
                        continue
                    candidates.append(full)
            except OSError:
                candidates = []

            preferred = [p for p in candidates if draft_name in os.path.basename(p)]
            ordered = sorted(preferred or candidates, key=os.path.getmtime, reverse=True)
            if ordered:
                src = ordered[0]
                if os.path.abspath(src) != output_path:
                    if os.path.isfile(output_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                    try:
                        shutil.move(src, output_path)
                    except OSError:
                        shutil.copy2(src, output_path)
                if os.path.isfile(output_path) and os.path.getsize(output_path) > 1024:
                    return
            time.sleep(1)

        if not (os.path.isfile(output_path) and os.path.getsize(output_path) > 1024):
            raise AutomationError(
                "导出完成但未在目标目录找到成片文件。"
                "请确认剪映默认导出目录与 output_path 一致"
            )

    def _export_window_roots(self) -> List:
        """当前「导出」窗口（完成页标题仍是「导出」）。"""
        roots = []
        app = getattr(self, "app", None)
        if app is not None:
            try:
                if (app.Name or "") == "导出" and app.Exists(0):
                    roots.append(app)
            except Exception:
                pass
            try:
                dlg = app.WindowControl(searchDepth=1, Name="导出")
                if dlg.Exists(0):
                    roots.append(dlg)
            except Exception:
                pass
        try:
            desktop = uia.GetRootControl()
            for win in desktop.GetChildren():
                try:
                    if (win.Name or "") == "导出" and win.Exists(0):
                        roots.append(win)
                except Exception:
                    continue
        except Exception:
            pass
        return roots

    def _cheap_named_control(self, root, name: str, depths=(2, 3, 4)):
        """按 Name 浅搜，Exists(0) 避免空等。"""
        if root is None or not name:
            return None
        for depth in depths:
            for factory_name in ("ButtonControl", "TextControl", "HyperlinkControl"):
                factory = getattr(root, factory_name, None)
                if factory is None:
                    continue
                try:
                    ctrl = factory(Name=name, searchDepth=depth)
                    if ctrl.Exists(0):
                        return ctrl
                except Exception:
                    continue
        return None

    def _latest_export_mp4(
        self,
        output_path: Optional[str],
        draft_name: str,
        since_ts: float,
    ) -> Optional[str]:
        """查找本次导出产生的 mp4。"""
        dirs = []
        if output_path:
            dirs.append(os.path.dirname(os.path.abspath(output_path)))
        seen = set()
        found = []
        for d in dirs:
            if not d or d in seen or not os.path.isdir(d):
                continue
            seen.add(d)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                if not name.lower().endswith(".mp4"):
                    continue
                full = os.path.join(d, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_mtime < since_ts - 8 or st.st_size < 1024:
                    continue
                found.append(full)
        if not found:
            return None
        preferred = [p for p in found if draft_name and draft_name in os.path.basename(p)]
        return max(preferred or found, key=os.path.getmtime)

    def _click_export_close_by_position(self, root) -> bool:
        """【已禁用】完成页坐标点「关闭」同样会误点其它程序，改由命名按钮路径处理。"""
        return False

    def _control_has_text(self, root, needle: str, max_depth: int = 6) -> bool:
        """浅搜可见文案，避免全树扫描卡死。"""
        if root is None or not needle:
            return False
        target = needle.lower()

        def _walk(node, depth: int) -> bool:
            if depth > max_depth:
                return False
            try:
                name = (node.Name or "").lower()
            except Exception:
                name = ""
            if needle.lower() in name or target in name:
                return True
            try:
                children = list(node.GetChildren())
            except Exception:
                return False
            for child in children:
                if _walk(child, depth + 1):
                    return True
            return False

        try:
            return _walk(root, 0)
        except Exception:
            return False

    def _is_export_success_page(self, root) -> bool:
        """剪映 5.9 导出完成后的发布引导页。"""
        markers = ("打开文件夹", "分享链接", "发布视频", "让更多人看到你的作品")
        if any(self._control_has_text(root, m, max_depth=5) for m in markers):
            return True
        # 完成页同时有「发布」和「关闭」；进度页一般没有这对按钮
        return self._control_has_text(root, "关闭", max_depth=5) and self._control_has_text(
            root, "发布", max_depth=5
        )

    def _click_named_close(self, root) -> bool:
        """只点文案为「关闭」的按钮，绝不点「发布」。"""
        btn = self._cheap_named_control(root, "关闭")
        if btn is None:
            return False
        try:
            btn.Click(simulateMove=False)
            return True
        except Exception:
            try:
                parent = btn.GetParentControl()
                if parent is not None:
                    parent.Click(simulateMove=False)
                    return True
            except Exception:
                return False
        return False

    def _click_export_succeed_close(self) -> bool:
        """导出完成页点「关闭」。禁止深搜 AutomationId，避免卡死 UIA。"""
        roots = self._export_window_roots()
        if not roots:
            return False
        for root in roots:
            opened = self._cheap_named_control(root, "打开文件夹")
            share = self._cheap_named_control(root, "分享链接")
            publish = self._cheap_named_control(root, "发布")
            # 完成页常见：打开文件夹 / 发布；部分版本只有「关闭」
            looks_done = (
                opened is not None
                or share is not None
                or publish is not None
                or self._is_export_success_page(root)
                or self._cheap_named_control(root, "关闭") is not None
            )
            if not looks_done:
                continue
            if self._click_named_close(root):
                return True
            # 文案可能是「完成」而非「关闭」
            for label in ("完成", "关闭"):
                btn = self._cheap_named_control(root, label)
                if btn is None:
                    continue
                try:
                    btn.Click(simulateMove=False)
                    return True
                except Exception:
                    try:
                        parent = btn.GetParentControl()
                        if parent is not None:
                            parent.Click(simulateMove=False)
                            return True
                    except Exception:
                        continue
            if self._click_export_close_by_position(root):
                return True
        return False

    def dismiss_export_success_ui(self, *, attempts: int = 10, interval: float = 1.0) -> bool:
        """导出完成后强制关掉「导出」完成页，避免草稿仍被占用。"""
        closed = False
        for i in range(max(1, attempts)):
            if self._click_export_succeed_close():
                print(f"[导出] 已关闭导出完成页（第 {i + 1} 次尝试）")
                closed = True
                break
            for root in self._export_window_roots():
                if self._click_export_close_by_position(root):
                    print(f"[导出] 已按位置关闭导出完成页（第 {i + 1} 次尝试）")
                    closed = True
                    break
            if closed:
                break
            # Esc / Alt+F4 兜底（部分完成页 Esc 无效）
            try:
                uia.SendKeys("{Esc}")
            except Exception:
                pass
            if i >= max(2, attempts // 2):
                try:
                    uia.SendKeys("%{F4}")
                except Exception:
                    pass
            time.sleep(interval)
        if not closed:
            print("[导出] 警告：多次尝试仍未能关闭导出完成页")
        # 尽量回到目录/主页，释放草稿锁
        try:
            self.get_window(activate=True)
        except Exception:
            pass
        try:
            self.switch_to_home()
        except Exception:
            pass
        time.sleep(0.5)
        return closed

    def export_draft(self, draft_name: str, output_path: Optional[str] = None, *,
                     resolution: Optional[ExportResolution] = None,
                     framerate: Optional[ExportFramerate] = None,
                     timeout: float = 1200) -> None:
        """导出指定的剪映草稿, **目前仅支持剪映6及以下版本**

        **注意: 需要确认有导出草稿的权限(不使用VIP功能或已开通VIP), 否则可能陷入死循环**

        Args:
            draft_name (`str`): 要导出的剪映草稿名称
            output_path (`str`, optional): 导出路径, 支持指向文件夹或直接指向文件, 不指定则使用剪映默认路径.
            resolution (`Export_resolution`, optional): 导出分辨率, 默认不改变剪映导出窗口中的设置.
            framerate (`Export_framerate`, optional): 导出帧率, 默认不改变剪映导出窗口中的设置.
            timeout (`float`, optional): 导出超时时间(秒), 默认为20分钟.

        Raises:
            `DraftNotFound`: 未找到指定名称的剪映草稿
            `AutomationError`: 剪映操作失败
        """
        print(f"开始导出 {draft_name} 至 {output_path}")
        export_since = time.time()
        self.dismiss_blocking_dialogs(rounds=2)
        self.get_window()
        self.switch_to_home()
        # 目录页扫草稿后可能再弹「草稿列表异常」，点草稿前必须关掉
        self.dismiss_blocking_dialogs(rounds=2)

        # 点击对应草稿
        print(f"[导出] 查找草稿: {draft_name}")
        draft_name_text = self.app.TextControl(
            searchDepth=2,
            Compare=ControlFinder.desc_matcher(f"HomePageDraftTitle:{draft_name}", exact=True)
        )
        if not draft_name_text.Exists(0):
            # 再关一次弹窗后重试找草稿（弹窗可能挡了列表刷新）
            self.dismiss_blocking_dialogs(rounds=2)
            time.sleep(0.5)
            draft_name_text = self.app.TextControl(
                searchDepth=2,
                Compare=ControlFinder.desc_matcher(f"HomePageDraftTitle:{draft_name}", exact=True)
            )
        if not draft_name_text.Exists(0):
            raise exceptions.DraftNotFound(f"未找到名为{draft_name}的剪映草稿")
        draft_btn = draft_name_text.GetParentControl()
        assert draft_btn is not None
        draft_btn.Click(simulateMove=False)
        print("[导出] 已打开草稿，等待编辑器就绪…")
        # 长草稿加载慢：优先锁定 MainWindow（勿误用仍存活的 HomePage）
        if not self._wait_edit_window(timeout=120.0):
            # 若仍被「草稿列表异常」挡住，关后再双击草稿重试
            self.dismiss_blocking_dialogs(rounds=3)
            time.sleep(0.6)
            try:
                # 二次查找：可能仍在目录页
                self.switch_to_home()
                self.dismiss_blocking_dialogs(rounds=2)
                draft_name_text2 = self.app.TextControl(
                    searchDepth=2,
                    Compare=ControlFinder.desc_matcher(f"HomePageDraftTitle:{draft_name}", exact=True)
                )
                if draft_name_text2.Exists(0):
                    btn2 = draft_name_text2.GetParentControl()
                    if btn2 is not None:
                        print("[导出] 未进编辑窗，二次双击草稿…")
                        btn2.DoubleClick(simulateMove=False)
            except Exception as exc:
                print(f"[导出] 二次打开草稿失败: {exc}")
            if not self._wait_edit_window(timeout=90.0):
                self.dismiss_blocking_dialogs(rounds=2)
                if not self._wait_edit_window(timeout=60.0):
                    raise AutomationError("打开草稿后未进入编辑窗口（MainWindow）")
        self.app.SetActive()
        try:
            self.app.SetTopmost()
        except Exception:
            pass

        export_btn = self._wait_titlebar_export_btn(timeout=90.0)
        if export_btn is None:
            raise AutomationError(
                "未在编辑窗口中找到导出按钮。"
                "请确认剪映已进入时间线编辑页、无遮挡弹窗，且版本支持自动化导出"
            )

        # 点击标题栏导出
        print("[导出] 点击标题栏导出按钮…")
        export_btn.Click(simulateMove=False)
        print("[导出] 已点标题栏导出，等待导出对话框…")

        # 等待导出对话框（ExportOkBtn）
        ok_btn = self._wait_control(
            lambda: self.app.TextControl(
                searchDepth=2,
                Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True),
            ),
            timeout=20.0,
        )
        if ok_btn is None:
            self.dismiss_blocking_dialogs(rounds=1)
            self.get_window()
            ok_btn = self.app.TextControl(
                searchDepth=2,
                Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True),
            )
            if not ok_btn.Exists(0):
                raise AutomationError("导出窗口未出现或未找到导出按钮")

        # 路径只快读一次；读不到则目录轮询
        export_path = self._read_export_path()
        if not export_path:
            print("警告: 未找到导出路径框，将改用 output_path/导出目录轮询收片")

        # 尽量设为指定分辨率/帧率（短超时；失败仅警告，不阻断导出）
        self._try_set_export_resolution_framerate(resolution, framerate)

        # 立刻点导出确认（勿再 SetActive 主窗，否则会关掉「导出」对话框）
        print("[导出] 点击导出确认按钮…")
        try:
            self.get_window(activate=False)
        except Exception:
            pass
        # 优先在「导出」子窗上找确认按钮
        export_root = self.app
        try:
            dlg = self.app.WindowControl(searchDepth=1, Name="导出")
            if dlg.Exists(0):
                export_root = dlg
                self.app = dlg
                self.app_status = "pre_export"
        except Exception:
            pass
        export_btn = export_root.TextControl(
            searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True)
        )
        if not export_btn.Exists(0):
            export_btn = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True)
            )
        if not export_btn.Exists(0):
            raise AutomationError("未在导出窗口中找到导出按钮")
        export_btn.Click(simulateMove=False)
        print("[导出] 已开始导出，等待完成…")
        time.sleep(1.2)

        # 先等文件落盘（长成片编码常超过 20 分钟）；完成页出现后再点关闭，进度中勿点以免取消
        st = time.time()
        last_ui = 0.0
        last_size = -1
        stable_since = None
        last_progress_log = 0.0
        while True:
            now = time.time()
            latest = self._latest_export_mp4(output_path, draft_name, export_since)
            size = 0
            if latest:
                try:
                    size = os.path.getsize(latest)
                except OSError:
                    size = 0
            growing = bool(latest) and size > last_size > 0
            if latest and size == last_size and size > 1024 * 1024:
                if stable_since is None:
                    stable_since = now
            else:
                stable_since = None
            if size > 0:
                last_size = size

            file_stable = bool(latest) and stable_since is not None and (now - stable_since) >= 4.0
            if now - last_progress_log >= 30.0:
                elapsed = int(now - st)
                if latest:
                    print(f"[导出] 已等待 {elapsed}s，成片 {os.path.basename(latest)} {size / 1024 / 1024:.1f}MB")
                else:
                    print(f"[导出] 已等待 {elapsed}s，尚未出现 mp4")
                last_progress_log = now

            if file_stable:
                # 成片已落盘：优先关掉完成页；关不掉也重试数次，避免草稿一直 .locked
                closed = False
                for attempt in range(8):
                    if self._click_export_succeed_close():
                        print(f"[导出] 已点击导出完成页「关闭」（attempt={attempt + 1}）")
                        closed = True
                        break
                    pos_ok = False
                    for root in self._export_window_roots():
                        if self._click_export_close_by_position(root):
                            print(f"[导出] 已按位置点击完成页「关闭」（attempt={attempt + 1}）")
                            pos_ok = True
                            break
                    if pos_ok:
                        closed = True
                        break
                    try:
                        uia.SendKeys("{Esc}")
                    except Exception:
                        pass
                    time.sleep(1.0)
                if not closed:
                    print("[导出] 成片已就绪但未能关闭完成页，继续收片（后续会再尝试关窗）")
                else:
                    print("[导出] 成片已就绪，继续收片")
                break
            if now - last_ui >= 5.0:
                last_ui = now
                # 仅完成页（有打开文件夹/发布）才点关闭，编码中不点以免取消
                if self._click_export_succeed_close():
                    print("[导出] 已点击导出完成页「关闭」")
                    break

            # 仍在编码：超时后再宽限 3 分钟
            limit = timeout
            if growing or (latest and not file_stable):
                limit = max(timeout, now - st + 180)
            if now - st > limit:
                raise AutomationError("导出超时, 时限为%d秒" % timeout)

            time.sleep(2.0)
        time.sleep(0.5)

        # 回到目录页（再强制关一次完成页）
        try:
            self.dismiss_export_success_ui(attempts=5, interval=0.8)
        except Exception:
            self.get_window(activate=True)
            try:
                self.switch_to_home()
            except AutomationError:
                pass
            time.sleep(0.5)

        if output_path is not None:
            self._settle_exported_file(
                export_path=export_path,
                output_path=output_path,
                draft_name=draft_name,
                since_ts=export_since,
            )

        print(f"导出 {draft_name} 至 {output_path} 完成")

    def switch_to_home(self) -> None:
        """切换到剪映主页"""
        self.dismiss_blocking_dialogs(rounds=1)
        if self.app_status == "home":
            return
        if self.app_status != "edit":
            raise AutomationError("仅支持从编辑模式切换到主页")
        close_btn = self.app.GroupControl(searchDepth=1, ClassName="TitleBarButton", foundIndex=3)
        close_btn.Click(simulateMove=False)
        time.sleep(0.8)
        self.get_window(activate=True)

    def _find_jianying_window(self, *, prefer: Literal["edit", "home", "any"] = "any"):
        """在桌面顶层查找剪映窗口；prefer=edit 时优先 MainWindow，避免误绑仍存活的 HomePage。"""
        desktop = uia.GetRootControl()
        home = None
        edit = None
        for win in desktop.GetChildren():
            try:
                if (win.Name or "") != "剪映专业版":
                    continue
                cls = (win.ClassName or "").lower()
            except Exception:
                continue
            if "mainwindow" in cls:
                edit = win
            elif "homepage" in cls:
                home = win
        if prefer == "edit" and edit is not None:
            return edit, "edit"
        if prefer == "home" and home is not None:
            return home, "home"
        if edit is not None:
            return edit, "edit"
        if home is not None:
            return home, "home"
        return None, None

    def _wait_edit_window(self, timeout: float = 90.0) -> bool:
        """等待并绑定编辑主窗 MainWindow。"""
        deadline = time.time() + timeout
        last_dismiss = 0.0
        while time.time() < deadline:
            now = time.time()
            # 不要每 0.2s 扫一遍 UIA（comtypes DEBUG 会刷爆控制台）
            if now - last_dismiss >= 2.0:
                self.dismiss_blocking_dialogs(rounds=1)
                last_dismiss = now
            win, status = self._find_jianying_window(prefer="edit")
            if win is not None and status == "edit" and win.Exists(0):
                self.app = win
                self.app_status = "edit"
                return True
            time.sleep(0.35)
        return False

    def _find_titlebar_export_btn(self):
        """在当前编辑窗查找标题栏导出按钮（兼容深度/文案差异）。"""
        if self.app is None:
            return None
        # 1) 官方自动化描述
        for depth in (2, 3, 4, 5, 8):
            try:
                btn = self.app.TextControl(
                    searchDepth=depth,
                    Compare=ControlFinder.desc_matcher("MainWindowTitleBarExportBtn"),
                )
                if btn.Exists(0):
                    return btn
            except Exception:
                pass
        # 2) 可见文案「导出」
        try:
            btn = self.app.TextControl(searchDepth=8, Name="导出")
            if btn.Exists(0):
                return btn
        except Exception:
            pass
        try:
            btn = self.app.ButtonControl(searchDepth=8, Name="导出")
            if btn.Exists(0):
                return btn
        except Exception:
            pass
        return None

    def _wait_titlebar_export_btn(self, timeout: float = 90.0):
        """长草稿加载后导出按钮会出现较晚，轮询等待。"""
        deadline = time.time() + timeout
        last_log = 0.0
        while time.time() < deadline:
            self.dismiss_blocking_dialogs(rounds=1)
            win, status = self._find_jianying_window(prefer="edit")
            if win is not None and status == "edit" and win.Exists(0):
                self.app = win
                self.app_status = "edit"
                btn = self._find_titlebar_export_btn()
                if btn is not None:
                    return btn
            now = time.time()
            if now - last_log >= 8.0:
                print(f"[导出] 仍在等待编辑页导出按钮… remaining={deadline - now:.0f}s")
                last_log = now
            time.sleep(0.25)
        return None

    def get_window(self, *, activate: bool = True) -> None:
        """寻找剪映窗口；activate=True 时置顶（等待阶段应传 False，避免拖慢）。"""
        if hasattr(self, "app") and self.app is not None:
            try:
                if self.app.Exists(0):
                    self.app.SetTopmost(False)
            except Exception:
                pass

        # 已在导出流程中优先编辑窗；目录页操作优先首页
        prefer: Literal["edit", "home", "any"] = "any"
        if getattr(self, "app_status", None) == "edit":
            prefer = "edit"
        elif getattr(self, "app_status", None) == "home":
            prefer = "home"

        win, status = self._find_jianying_window(prefer=prefer)
        if win is None:
            # 回退：原 Compare 查找
            self.app = uia.WindowControl(searchDepth=1, Compare=self.__jianying_window_cmp)
            if not self.app.Exists(0):
                raise AutomationError("剪映窗口未找到")
        else:
            self.app = win
            self.app_status = status or "home"

        # 寻找可能存在的导出窗口
        try:
            export_window = self.app.WindowControl(searchDepth=1, Name="导出")
            if export_window.Exists(0):
                self.app = export_window
                self.app_status = "pre_export"
        except Exception:
            pass

        if activate:
            try:
                self.app.SetActive()
                self.app.SetTopmost()
            except Exception:
                pass

    def __jianying_window_cmp(self, control: uia.WindowControl, depth: int) -> bool:
        if control.Name != "剪映专业版":
            return False
        if "MainWindow".lower() in control.ClassName.lower():
            self.app_status = "edit"
            return True
        if "HomePage".lower() in control.ClassName.lower():
            self.app_status = "home"
            return True
        return False
