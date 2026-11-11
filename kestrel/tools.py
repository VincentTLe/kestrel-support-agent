"""What a tool is, and the one function that runs any tool.

A tool is a name, a description, a pydantic model for its arguments and a
handler. Features (kestrel/features) build their own tools. A failed call is
returned to the model as data ({"error": ...}) and never raised, so one bad
call cannot end the chat.

Arguments named in a tool's customer_supplied must be values the customer
typed in this conversation (hard guardrail H2), so the model cannot guess an
email, an order number or a name, even a correct one.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Callable

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from .orders import normalize_email, normalize_order_number

log = logging.getLogger(__name__)

Email = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]

EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ORDER_NUMBER_IN_TEXT = re.compile(r"\b(?:KC[\s-]?)?\d{5}\b", re.IGNORECASE)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[\w'-]+", text.casefold()))


def _typed(pattern: re.Pattern, normalize: Callable[[str], str]) -> Callable[[str, str], bool]:
    return lambda value, text: normalize(value) in {normalize(found) for found in pattern.findall(text)}


# How to tell that the customer typed a value, per argument name.
CUSTOMER_VALUES: dict[str, Callable[[str, str], bool]] = {
    "email": _typed(EMAIL_IN_TEXT, normalize_email),
    "order_number": _typed(ORDER_NUMBER_IN_TEXT, normalize_order_number),
    # Emails are removed first: marcus.reed@example.com must not count as having typed "Marcus Reed".
    "customer_name": lambda value, text: bool(_words(value))
    and f" {_words(value)} " in f" {_words(EMAIL_IN_TEXT.sub(' ', text))} ",
}


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any], dict]
    customer_supplied: tuple[str, ...] = ()  # arguments the customer must have typed

    def schema(self) -> dict:
        """The tool in the OpenAI-compatible function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


def run_tool(tools: dict[str, Tool], name: str, raw_arguments: str, customer_text: str | None = None) -> dict:
    """Validate the model's arguments, run the tool, and always return a dict.

    With customer_text, arguments in tool.customer_supplied must appear in it.
    """
    tool = tools.get(name)
    if tool is None:
        return {"error": "unknown_tool", "message": f"There is no tool named {name!r}."}
    try:
        args = tool.args_model.model_validate(json.loads(raw_arguments or "{}"))
    except json.JSONDecodeError:
        return {"error": "invalid_arguments", "message": "Arguments were not valid JSON."}
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
        )
        return {"error": "invalid_arguments", "message": problems}
    if customer_text is not None:
        missing = [
            field
            for field in tool.customer_supplied
            if not CUSTOMER_VALUES[field](str(getattr(args, field)), customer_text)
        ]
        if missing:
            return {
                "error": "not_provided_by_customer",
                "message": f"The customer has not typed this {' or '.join(missing)} in the conversation. "
                "Ask the customer for it; never guess or infer it.",
            }
    try:
        return tool.handler(args)
    except Exception:  # the customer should get an apology, not a stack trace
        log.exception("tool %s failed", name)
        return {"error": "tool_failed", "message": "The tool hit an internal error. Apologize; do not guess."}
