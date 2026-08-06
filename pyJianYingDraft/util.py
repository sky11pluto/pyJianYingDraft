"""辅助函数，主要与模板模式有关"""

import inspect
import typing

from typing import Union, Type
from typing import List, Dict, Any

JsonExportable = Union[int, float, bool, str, List["JsonExportable"], Dict[str, "JsonExportable"]]

def provide_ctor_defaults(cls: Type) -> Dict[str, Any]:
    """为构造函数提供默认值，以绕开构造函数的参数限制"""

    signature = inspect.signature(cls.__init__)
    provided_defaults: Dict[str, Any] = {}

    for name, param in signature.parameters.items():
        if name == 'self': continue
        if param.default is not inspect.Parameter.empty: continue

        if param.annotation is int or param.annotation is float:
            provided_defaults[name] = 0
        elif param.annotation is str:
            provided_defaults[name] = ""
        elif param.annotation is bool:
            provided_defaults[name] = False
        else:
            raise ValueError(f"Unsupported parameter type: {param.annotation}")

    return provided_defaults

def _collect_type_hints(obj: object) -> Dict[str, Type]:
    """收集 obj 的所有类型注解，包括通过 __mro__ 继承的。

    兼容 `from __future__ import annotations` 的情况：
    优先使用 typing.get_type_hints() 解析字符串形式的注解。
    """
    type_hints: Dict[str, Type] = {}
    for cls in obj.__class__.__mro__:
        if '__annotations__' in cls.__dict__:
            type_hints.update(cls.__annotations__)

    # ⚠️ 关键修复：尝试用 typing.get_type_hints 解析字符串形式的注解
    # 在 `from __future__ import annotations` 模式下，__annotations__ 存的是字符串
    # 此时 type_hints 中的值是字符串（如 "int"），无法直接用 int() 构造
    # get_type_hints 会把它们解析为真实的类型对象
    try:
        resolved = typing.get_type_hints(obj.__class__)
        for k, v in resolved.items():
            # 跳过 typing 泛型（List、Dict、Optional 等），它们不是具体类型
            if hasattr(v, '__origin__'):
                continue
            type_hints[k] = v
    except Exception:
        # 如果 get_type_hints 失败（例如前向引用未解析），继续使用原始 type_hints
        pass

    return type_hints

def assign_attr_with_json(obj: object, attrs: List[str], json_data: Dict[str, Any]):
    """根据json数据赋值给指定的对象属性

    若有复杂类型，则尝试调用其`import_json`方法进行构造
    """
    type_hints = _collect_type_hints(obj)

    for attr in attrs:
        # ⚠️ 修复：用 .get() 避免 attr 不在 type_hints 中时 KeyError
        # 当 attr 在 type_hints 中不存在时，回退到直接使用 json_data 中的值
        target_type = type_hints.get(attr)
        if target_type is not None and hasattr(target_type, 'import_json'):
            obj.__setattr__(attr, target_type.import_json(json_data[attr]))
        elif target_type is not None:
            # 尝试调用 target_type(json_data[attr]) 构造对象
            try:
                obj.__setattr__(attr, target_type(json_data[attr]))
            except (TypeError, ValueError):
                # 构造失败时直接赋值原始 dict
                obj.__setattr__(attr, json_data[attr])
        else:
            # 回退：直接赋值（让对象属性保持原始 JSON 值）
            obj.__setattr__(attr, json_data[attr])

def export_attr_to_json(obj: object, attrs: List[str]) -> Dict[str, JsonExportable]:
    """将对象属性导出为json数据

    若有复杂类型，则尝试调用其`export_json`方法进行导出
    """
    json_data: Dict[str, Any] = {}
    for attr in attrs:
        if hasattr(getattr(obj, attr), 'export_json'):
            json_data[attr] = getattr(obj, attr).export_json()
        else:
            json_data[attr] = getattr(obj, attr)
    return json_data
