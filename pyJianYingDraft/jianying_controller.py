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
        # 删除任务草稿或草稿目录异常后，启动时常弹；点确认即可，一般不影响本次导出
        "草稿丢失",
        "草稿已丢失",
        "部分草稿",
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

    def __init__(self):
        """初始化剪映控制器, 此时剪映应该处于目录页"""
        self.get_window()
        self.dismiss_blocking_dialogs()

    def dismiss_blocking_dialogs(self, rounds: int = 1) -> int:
        """轻量关闭干扰弹窗（音频设备 / 草稿丢失等）。

        优先按桌面顶层窗口标题匹配；若标题仅为「提示」等，再在剪映主窗下浅搜文案后点确认。
        """
        closed = 0
        for _ in range(max(1, rounds)):
            hit = False
            try:
                desktop = uia.GetRootControl()
                for win in desktop.GetChildren():
                    try:
                        name = (win.Name or "")
                    except Exception:
                        continue
                    if not name or not any(k in name for k in self._BLOCKING_POPUP_KEYWORDS):
                        continue
                    if self._click_dismiss_button_under(win):
                        closed += 1
                        hit = True
                        print(f"已关闭剪映提示弹窗: {name}")
                        time.sleep(0.15)
            except Exception:
                pass
            # 主窗内嵌弹窗：标题常为「提示」，正文才含「草稿丢失」
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

    def _dismiss_by_message_under(self, root) -> bool:
        """在根节点下浅搜含关键词的文案，再点同级/父级确认按钮。"""
        if root is None:
            return False
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
                # 文案控件本身或父级对话框上点确认
                if self._click_dismiss_button_under(child):
                    print(f"已关闭剪映提示弹窗(文案): {name[:40]}")
                    return True
                parent = None
                try:
                    parent = child.GetParentControl()
                except Exception:
                    parent = None
                if parent is not None and self._click_dismiss_button_under(parent):
                    print(f"已关闭剪映提示弹窗(父级): {name[:40]}")
                    return True
            # 再下一层（常见：Window/Group -> Text）
            try:
                for grand in child.GetChildren():
                    try:
                        gname = grand.Name or ""
                    except Exception:
                        continue
                    if not gname or not any(k in gname for k in self._BLOCKING_POPUP_KEYWORDS):
                        continue
                    if self._click_dismiss_button_under(child) or self._click_dismiss_button_under(grand):
                        print(f"已关闭剪映提示弹窗(嵌套): {gname[:40]}")
                        return True
            except Exception:
                continue
        return False

    def _click_dismiss_button_under(self, root) -> bool:
        """仅在已锁定的弹窗根节点下浅搜确认按钮（Exists(0) 避免数秒空等）。"""
        if root is None:
            return False
        for btn_name in self._DISMISS_BUTTON_NAMES:
            try:
                btn = root.ButtonControl(Name=btn_name, searchDepth=3)
                if btn.Exists(0):
                    btn.Click(simulateMove=False)
                    return True
            except Exception:
                pass
            try:
                txt = root.TextControl(Name=btn_name, searchDepth=3)
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
        self.dismiss_blocking_dialogs()
        self.get_window()
        self.switch_to_home()

        # 点击对应草稿
        print(f"[导出] 查找草稿: {draft_name}")
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
        if not self._wait_edit_window(timeout=90.0):
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

        # 等待导出完成
        st = time.time()
        last_dismiss = 0.0
        while True:
            now = time.time()
            if now - last_dismiss >= 8.0:
                self.dismiss_blocking_dialogs(rounds=1)
                last_dismiss = now
            self.get_window(activate=False)
            if self.app_status != "pre_export":
                if output_path and os.path.isfile(output_path) and os.path.getsize(output_path) > 1024:
                    break
                time.sleep(0.8)
                if time.time() - st > timeout:
                    raise AutomationError("导出超时, 时限为%d秒" % timeout)
                continue

            succeed_close_btn = self.app.TextControl(
                searchDepth=2,
                Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn"),
            )
            if succeed_close_btn.Exists(0):
                succeed_close_btn.Click(simulateMove=False)
                break

            if time.time() - st > timeout:
                raise AutomationError("导出超时, 时限为%d秒" % timeout)

            time.sleep(0.8)
        time.sleep(0.5)

        # 回到目录页
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
        while time.time() < deadline:
            self.dismiss_blocking_dialogs(rounds=1)
            win, status = self._find_jianying_window(prefer="edit")
            if win is not None and status == "edit" and win.Exists(0):
                self.app = win
                self.app_status = "edit"
                return True
            time.sleep(0.2)
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
