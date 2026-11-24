import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        if record.name == "uvicorn.access":
            entry["message"] = "HTTP request"
            if isinstance(record.args, tuple) and len(record.args) == 5:
                client, method, target, version, status = record.args
                entry.update(
                    client=client,
                    method=method,
                    path=str(target).partition("?")[0],
                    http_version=version,
                    status=status,
                )
        else:
            entry["message"] = record.getMessage()
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)
