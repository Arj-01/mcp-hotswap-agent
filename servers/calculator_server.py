"""Calculator MCP server — math expressions, unit conversions, bill splitting, loan EMI."""
import ast
import math
import re
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calculator")

# ------------------------------------------------------------------ safe eval

_SAFE_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "log": math.log,
    "abs": abs,
    "round": round,
}
_SAFE_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.Mod: lambda a, b: a % b,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value  # preserve int vs float so round(x, 2) works
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        return _SAFE_OPS[op_type](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        raise ValueError("Unsupported unary operator")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        name = node.func.id
        if name not in _SAFE_FUNCS:
            raise ValueError(f"Function not allowed: {name!r}")
        return float(_SAFE_FUNCS[name](*[_eval_node(a) for a in node.args]))
    raise ValueError(f"Unsupported expression element: {type(node).__name__}")


def _safe_eval(expr: str) -> float:
    # Preprocess "X% of Y" → X/100*Y
    expr = re.sub(
        r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)",
        r"\1/100*\2",
        expr,
        flags=re.IGNORECASE,
    )
    tree = ast.parse(expr.strip(), mode="eval")
    return _eval_node(tree.body)


# ------------------------------------------------------------------ unit table

_UNIT_CONVERSIONS: dict[tuple[str, str], object] = {
    ("celsius",  "fahrenheit"): lambda x: x * 9 / 5 + 32,
    ("fahrenheit", "celsius"): lambda x: (x - 32) * 5 / 9,
    ("km",       "miles"):      lambda x: x * 0.621371,
    ("miles",    "km"):         lambda x: x * 1.60934,
    ("kg",       "lbs"):        lambda x: x * 2.20462,
    ("lbs",      "kg"):         lambda x: x / 2.20462,
    ("meters",   "feet"):       lambda x: x * 3.28084,
    ("feet",     "meters"):     lambda x: x / 3.28084,
    ("liters",   "gallons"):    lambda x: x * 0.264172,
    ("gallons",  "liters"):     lambda x: x / 0.264172,
    ("inches",   "cm"):         lambda x: x * 2.54,
    ("cm",       "inches"):     lambda x: x / 2.54,
    ("inr",      "usd"):        lambda x: x / 83.0,
    ("usd",      "inr"):        lambda x: x * 83.0,
}

# ------------------------------------------------------------------ tools

@mcp.tool()
def calculate(expression: str) -> str:
    """Safely evaluate a math expression. Supports +,-,*,/,**,%, sqrt(), sin(), cos(), log(), abs(), round(), and '15% of 340' syntax."""
    try:
        result = _safe_eval(expression)
        # Show integer if result is whole
        formatted = int(result) if result == int(result) else round(result, 8)
        return f"{expression} = {formatted}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def percentage(value: float, percent: float) -> str:
    """Calculate what percent% of value is. Example: percentage(500, 18) -> '18% of 500 = 90.0'"""
    result = value * percent / 100
    return f"{percent}% of {value} = {round(result, 4)}"


@mcp.tool()
def split_bill(total: float, people: int, tip_percent: float = 0.0) -> str:
    """Split a restaurant bill among people with an optional tip percentage. Returns a full breakdown."""
    if people <= 0:
        return "Error: people must be at least 1"
    tip_amount = total * tip_percent / 100
    total_with_tip = total + tip_amount
    per_person = total_with_tip / people
    return (
        f"Bill: ${total:.2f} | Tip ({tip_percent}%): ${tip_amount:.2f} | "
        f"Total: ${total_with_tip:.2f} | Per person ({people}): ${per_person:.2f}"
    )


@mcp.tool()
def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    """Convert between units. Supported: celsius/fahrenheit, km/miles, kg/lbs, meters/feet, liters/gallons, inches/cm, inr/usd."""
    key = (from_unit.lower().strip(), to_unit.lower().strip())
    if key not in _UNIT_CONVERSIONS:
        supported = ", ".join(f"{a}->{b}" for a, b in _UNIT_CONVERSIONS)
        return f"Unsupported conversion: {from_unit} -> {to_unit}. Supported: {supported}"
    result = _UNIT_CONVERSIONS[key](value)
    return f"{value} {from_unit} = {round(result, 6)} {to_unit}"


@mcp.tool()
def loan_emi(principal: float, annual_rate: float, years: int) -> str:
    """Calculate monthly EMI, total interest, and total payment for a loan. annual_rate is in percent (e.g. 8.5 for 8.5%)."""
    if annual_rate <= 0 or years <= 0 or principal <= 0:
        return "Error: principal, annual_rate, and years must all be positive"
    r = annual_rate / (12 * 100)   # monthly interest rate
    n = years * 12                  # total months
    emi = principal * r * (1 + r) ** n / ((1 + r) ** n - 1)
    total_payment = emi * n
    total_interest = total_payment - principal
    return (
        f"Monthly EMI: {emi:.2f} | "
        f"Total Interest: {total_interest:.2f} | "
        f"Total Payment: {total_payment:.2f}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
