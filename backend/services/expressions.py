"""受控数学表达式：模型只写公式，求值与渲染由我们控制。

互动教具的读数与曲线必须由公式算出来，否则滑块只是玩具。但让模型直接写
JavaScript 等于把可执行代码交给它——不可验证、不可审计。所以这里定一层很窄的
表达式语言：

- 变量：工具自己的参数与已定义的中间量，名字来自校验通过的键名白名单；
- 运算：``+ - * / % **``、一元正负、括号；
- 函数：sqrt/sin/cos/tan/asin/acos/atan/log/log10/exp/abs/min/max/pow/floor/ceil；
- 常量：pi、e。

实现方式："解析成 AST → 逐节点判断白名单"，然后从同一棵 AST 生成两种输出：
Python 端求值（用于服务端校验与预览）与 JavaScript 源码（用于产出的 HTML）。
两边同源，因此产出的公式一定是校验通过的那一条，不做字符串拼接替换。
"""

from __future__ import annotations

import ast
import math
from typing import Any, Callable, Mapping

__all__ = [
    "ExpressionError",
    "compile_expression",
    "evaluate",
    "referenced_names",
    "to_javascript",
]


class ExpressionError(ValueError):
    """表达式非法：语法错误、用了白名单外的名字或运算。"""


# 允许的函数：名字 -> (Python 实现, JavaScript 实现)
_FUNCTIONS: dict[str, tuple[Callable[..., Any], str]] = {
    "sqrt": (math.sqrt, "Math.sqrt"),
    "sin": (math.sin, "Math.sin"),
    "cos": (math.cos, "Math.cos"),
    "tan": (math.tan, "Math.tan"),
    "asin": (math.asin, "Math.asin"),
    "acos": (math.acos, "Math.acos"),
    "atan": (math.atan, "Math.atan"),
    "atan2": (math.atan2, "Math.atan2"),
    "log": (math.log, "Math.log"),
    "log10": (math.log10, "Math.log10"),
    "exp": (math.exp, "Math.exp"),
    "abs": (abs, "Math.abs"),
    "min": (min, "Math.min"),
    "max": (max, "Math.max"),
    "pow": (math.pow, "Math.pow"),
    "floor": (math.floor, "Math.floor"),
    "ceil": (math.ceil, "Math.ceil"),
}

# 允许的常量：名字 -> (Python 值, JavaScript 源码)
_CONSTANTS: dict[str, tuple[float, str]] = {
    "pi": (math.pi, "Math.PI"),
    "e": (math.e, "Math.E"),
}

_MAX_LENGTH = 400
_MAX_DEPTH = 24

_BINARY: dict[type, tuple[str, Callable[[float, float], float]]] = {
    ast.Add: ("+", lambda a, b: a + b),
    ast.Sub: ("-", lambda a, b: a - b),
    ast.Mult: ("*", lambda a, b: a * b),
    ast.Div: ("/", lambda a, b: a / b),
    ast.Mod: ("%", lambda a, b: a % b),
    ast.Pow: ("**", lambda a, b: a**b),
}


def _parse(expression: str) -> ast.Expression:
    text = str(expression or "").strip()
    if not text:
        raise ExpressionError("表达式为空")
    if len(text) > _MAX_LENGTH:
        raise ExpressionError(f"表达式过长（{len(text)} > {_MAX_LENGTH}）")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"表达式语法错误：{exc.msg}") from exc
    if _depth(tree) > _MAX_DEPTH:
        raise ExpressionError("表达式嵌套过深")
    return tree


def _depth(node: ast.AST, level: int = 0) -> int:
    children = list(ast.iter_child_nodes(node))
    return level if not children else max(_depth(child, level + 1) for child in children)


def referenced_names(expression: str) -> set[str]:
    """表达式真正依赖的变量名。

    排除两类名字，否则调用方会被误导成"公式引用了 sqrt/pi 这些变量"：
    - 白名单函数名（出现在调用位置的名字）；
    - 常量 pi、e。
    """
    tree = _parse(expression).body
    function_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in function_names
        and node.id not in _CONSTANTS
    }


def _eval_node(node: ast.AST, values: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError("只允许数字常量")
        return float(node.value)

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return float(_CONSTANTS[node.id][0])
        if node.id in values:
            return float(values[node.id])
        raise ExpressionError(f"未定义的名称：{node.id}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, values)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        raise ExpressionError("不支持的一元运算")

    if isinstance(node, ast.BinOp):
        symbol = _BINARY.get(type(node.op))
        if symbol is None:
            raise ExpressionError("不支持的运算符")
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        try:
            return float(symbol[1](left, right))
        except ZeroDivisionError as exc:
            raise ExpressionError("除数为零") from exc
        except (OverflowError, ValueError) as exc:
            raise ExpressionError(f"数值溢出：{exc}") from exc

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ExpressionError("只允许使用白名单内的数学函数")
        if node.keywords:
            raise ExpressionError("函数不支持关键字参数")
        arguments = [_eval_node(argument, values) for argument in node.args]
        try:
            return float(_FUNCTIONS[node.func.id][0](*arguments))
        except ZeroDivisionError as exc:
            raise ExpressionError("除数为零") from exc
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExpressionError(f"函数 {node.func.id} 计算失败：{exc}") from exc

    raise ExpressionError(f"不允许的语法：{type(node).__name__}")


def _js_node(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError("只允许数字常量")
        return repr(float(node.value))

    if isinstance(node, ast.Name):
        return _CONSTANTS[node.id][1] if node.id in _CONSTANTS else node.id

    if isinstance(node, ast.UnaryOp):
        inner = _js_node(node.operand)
        if isinstance(node.op, ast.USub):
            return f"(-{inner})"
        if isinstance(node.op, ast.UAdd):
            return inner
        raise ExpressionError("不支持的一元运算")

    if isinstance(node, ast.BinOp):
        symbol = _BINARY.get(type(node.op))
        if symbol is None:
            raise ExpressionError("不支持的运算符")
        # ** 在 JS 里是同名运算符（ES2016），与 Python 语义一致
        return f"({_js_node(node.left)} {symbol[0]} {_js_node(node.right)})"

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ExpressionError("只允许使用白名单内的数学函数")
        if node.keywords:
            raise ExpressionError("函数不支持关键字参数")
        arguments = ", ".join(_js_node(argument) for argument in node.args)
        return f"{_FUNCTIONS[node.func.id][1]}({arguments})"

    raise ExpressionError(f"不允许的语法：{type(node).__name__}")


def evaluate(expression: str, values: Mapping[str, float] | None = None) -> float:
    """服务端求值：用于校验公式可算、以及生成预览读数。"""
    return _eval_node(_parse(expression).body, values or {})


def to_javascript(expression: str) -> str:
    """把表达式翻成 JS 源码（变量名原样引用，常量与函数映射到 Math.*）。"""
    return _js_node(_parse(expression).body)


def compile_expression(
    expression: str,
    names: set[str],
) -> tuple[Callable[[Mapping[str, float]], float], str]:
    """校验并编译：返回 (Python 求值函数, JS 源码)。

    ``names`` 是允许引用的变量集合（工具的参数与中间量）。引用未知名会被拒 ——
    这是挡住"编造公式"的关键一道闸：模型写的公式只要引用了不存在的量，
    或者语法/函数越界，都在这里失败。
    """
    used = referenced_names(expression)
    unknown = {name for name in used if name not in names and name not in _CONSTANTS}
    if unknown:
        raise ExpressionError("公式引用了未定义的量：" + "、".join(sorted(unknown)))
    js_source = to_javascript(expression)

    def run(values: Mapping[str, float]) -> float:
        return evaluate(expression, values)

    return run, js_source
