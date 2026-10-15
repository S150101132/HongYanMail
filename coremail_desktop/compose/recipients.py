from email.utils import getaddresses
import re
from coremail_desktop.errors import AppError

def normalize_recipients(values: tuple[str, ...]) -> tuple[str, ...]:
    result, seen = [], set()
    for value in values:
        if '\r' in value or '\n' in value: raise AppError('INVALID_RECIPIENT')
        if not value.strip(): continue
        addresses = getaddresses([value.replace(';', ',')])
        for _, address in addresses:
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', address): raise AppError('INVALID_RECIPIENT')
            if address.casefold() not in seen:
                seen.add(address.casefold()); result.append(address)
    return tuple(result)
