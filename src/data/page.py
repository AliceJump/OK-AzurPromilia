from __future__ import annotations

import collections
import traceback
from typing import Any, ClassVar


class Page:
    """页面定义与寻路拓扑节点。

    类属性 `all_pages` 静态保存所有预构建的页面实例。
    声明所有页面与调用 `link` 记录边后，需手动调用 `Page.build()` 正式构建静态图。
    """

    all_pages: ClassVar[dict[str, Page]] = {}

    @classmethod
    def get(cls, name_or_page: Page | str) -> Page:
        """根据名称或实例获取已注册的 Page。

        Args:
            name_or_page: Page 实例或页面名称字符串。

        Returns:
            Page: 对应的页面实例。

        Raises:
            KeyError: 页面未注册。
        """
        if isinstance(name_or_page, Page):
            return name_or_page
        if name_or_page in cls.all_pages:
            return cls.all_pages[name_or_page]
        raise KeyError(
            f"Page '{name_or_page}' not found. Registered pages: {list(cls.all_pages.keys())}"
        )

    @classmethod
    def clear_connection(cls) -> None:
        """清除所有页面上的 parent 寻路指针。"""
        for page in cls.all_pages.values():
            page.parent = None

    @classmethod
    def clear_all_pages(cls) -> None:
        """清空全局页面注册表及拓扑连接（主要供测试隔离使用）。"""
        for page in cls.all_pages.values():
            page._pending_links.clear()
            page.links.clear()
            page.parent = None
        cls.all_pages.clear()

    @classmethod
    def build(cls) -> None:
        """正式建立所有 Page 之间的静态拓扑图。

        在所有 Page 实例化并完成 `link(...)` 边声明后手动调用。
        将解析所有预先记录的边（无论目标页面是晚于源页面定义还是以字符串形式引用），
        并填充每个 Page 的 `links` 映射。
        """
        for page in cls.all_pages.values():
            page.links.clear()

        for page in cls.all_pages.values():
            for button, destination, back_button in page._pending_links:
                try:
                    dest_page = cls.get(destination)
                except KeyError as exc:
                    raise KeyError(
                        f"Failed to build link from '{page.name}' to '{destination}': "
                        f"destination page is not registered."
                    ) from exc
                page.links[dest_page] = button
                if back_button is not None:
                    dest_page.links[page] = back_button

    @classmethod
    def dump_graph(cls) -> dict[str, dict[str, Any]]:
        """导出当前拓扑图结构的字典形式，便于调试与校验。"""
        graph: dict[str, dict[str, Any]] = {}
        for name, page in cls.all_pages.items():
            graph[name] = {dst.name: btn for dst, btn in page.links.items()}
        return graph

    @classmethod
    def validate_graph(cls) -> list[str]:
        """校验拓扑图的完整性与有效性，返回发现的潜在问题列表。"""
        issues: list[str] = []
        for name, page in cls.all_pages.items():
            if page.check_feature is None:
                issues.append(f"Page '{name}' has no check_feature.")
            if not page.links:
                issues.append(f"Page '{name}' has no outgoing links.")
        return issues

    @classmethod
    def init_connection(cls, destination: Page | str) -> None:
        """初始化页面间的 BFS 寻路连接。

        从目标页面开始反向广度优先搜索，为所有可达页面建立
        指向最短路径下一跳页面的 parent 指针。

        Args:
            destination: 目标页面实例或页面名称。
        """
        dest_page = cls.get(destination)
        cls.clear_connection()

        queue: collections.deque[Page] = collections.deque([dest_page])
        visited: set[Page] = {dest_page}

        while queue:
            current = queue.popleft()
            for page in cls.all_pages.values():
                if page in visited:
                    continue
                if current in page.links:
                    page.parent = current
                    visited.add(page)
                    queue.append(page)

    @classmethod
    def iter_pages(cls):
        """遍历所有已注册的页面。"""
        return cls.all_pages.values()

    @classmethod
    def iter_check_features(cls):
        """遍历所有页面的检查特征。"""
        for page in cls.all_pages.values():
            if page.check_feature is not None:
                yield page.check_feature

    def __init__(self, check_feature: Any = None, name: str | None = None):
        self.check_feature = check_feature
        self.links: dict[Page, Any] = {}
        self._pending_links: list[tuple[Any, Page | str, Any | None]] = []
        self.parent: Page | None = None

        if name is not None:
            self.name = name
        else:
            self.name = self._resolve_caller_name()

        Page.all_pages[self.name] = self

    @staticmethod
    def _resolve_caller_name() -> str:
        """尝试从调用栈中解析赋值变量名作为页面名称（兜底保证不崩溃）。"""
        try:
            frames = traceback.extract_stack()
            # 倒数第 1 帧为当前方法，倒数第 2 帧为 __init__，倒数第 3 帧为实例化处
            if len(frames) >= 3 and frames[-3].line:
                line = frames[-3].line
                eq_idx = line.find("=")
                if eq_idx != -1:
                    candidate = line[:eq_idx].strip()
                    if ":" in candidate:
                        candidate = candidate.split(":", 1)[0].strip()
                    if candidate.isidentifier():
                        return candidate
        except Exception:
            pass
        return f"page_{id(object())}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Page):
            return False
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __repr__(self) -> str:
        return f"Page({self.name})"

    def __str__(self) -> str:
        return self.name

    def link(
        self,
        button: Any,
        destination: Page | str,
        back_button: Any = None,
    ) -> Page:
        """记录从当前页面前往 destination 的导航动作，支持链式调用。

        此方法仅负责记录边信息。待所有 Page 注册完成后，
        需手动调用 `Page.build()` 解析并正式建立拓扑图。

        Args:
            button: 点击目标（Box、特征名称、坐标或无参回调函数）。
            destination: 目标页面实例或页面名称（可引用尚未定义的 Page 名称）。
            back_button: 可选的反向返回动作。

        Returns:
            Page: 当前页面实例（self），支持链式调用。
        """
        self._pending_links.append((button, destination, back_button))
        return self
