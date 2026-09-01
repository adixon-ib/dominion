# Log Operations

Mark a point in a log, then dump or count entries since that mark. Available on both `Appliance` and container objects.

```python
# Mark the current end of infoblox.log
mark = app.mark_infoblox_log()

# ... do something that generates log entries ...

# Dump new entries since the mark to a local file
dump = app.dump_infoblox_log(dest="/tmp/logs/", since=mark)
print(f"{dump.line_count} new lines")

# Count occurrences of a pattern since the mark
count = app.count_infoblox_log("token_license", since=mark)

# Works with any log path, not just infoblox.log
mark = app.mark_log("/infoblox/var/syslog")
dump = app.dump_log("/infoblox/var/syslog", dest="/tmp/logs/", since=mark, grep="named")
```

Methods:
- `mark_log(path)` / `mark_infoblox_log()` -- returns a `LogMark` (log path + line count at time of mark)
- `dump_log(path, dest=, since=, grep=)` / `dump_infoblox_log(dest=, since=, grep=)` -- returns a `LogDump` with `.text`, `.lines`, `.line_count`
- `count_log(path, pattern, since=)` / `count_infoblox_log(pattern, since=)` -- returns an `int`
