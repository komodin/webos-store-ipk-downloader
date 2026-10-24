#!/usr/bin/env python3
"""Download IPK packages from LG Content Store through a webOS TV over SSH.

This file contains no environment-specific addresses, users, countries, or
paths. Sensitive TV headers stay in memory and are sent through SSH standard
input. The program does not change the region configured on the TV.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DEFAULT_APP_KEY = "dd9c5815-1dfe-4de2-affa-ef85c5785304"
SDX_URI = "luna://com.webos.service.sdx/getHttpHeaderForServiceRequest"
USER_AGENT = "Mozilla/5.0 (webOS.TV) AppleWebKit/537.36 LGBrowser/8.00"


class ToolError(RuntimeError):
    pass


def log(message: str, verbose: bool) -> None:
    if verbose:
        print(message, file=sys.stderr)


def parse_json(text: str) -> Any:
    candidate = text.strip().lstrip("\ufeff")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start = candidate.find("(")
    end = candidate.rfind(")")
    if start >= 0 and end > start:
        try:
            return json.loads(candidate[start + 1 : end])
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", candidate):
        try:
            value, _ = decoder.raw_decode(candidate[match.start() :])
            return value
        except json.JSONDecodeError:
            continue
    raise ToolError("The response does not contain valid JSON")


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def find_value(value: Any, names: set[str]) -> Any | None:
    wanted = {name.casefold() for name in names}
    for node in walk(value):
        if not isinstance(node, dict):
            continue
        for key, item in node.items():
            if key.casefold() in wanted and item not in (None, ""):
                return item
    return None


def parse_target(target: str, country_arg: str | None) -> tuple[str, str]:
    app_ref = target.strip()
    country = country_arg.upper() if country_arg else None

    if "://" in app_ref:
        parsed = urllib.parse.urlparse(app_ref)
        match = re.fullmatch(
            r"([a-z]{2})\.lgappstv\.com", (parsed.hostname or "").casefold()
        )
        if not match:
            raise ToolError("The URL must belong to <country>.lgappstv.com")
        url_country = match.group(1).upper()
        if country and country != url_country:
            raise ToolError(
                f"The URL belongs to {url_country}, but --country specifies {country}"
            )
        country = url_country
        query = urllib.parse.parse_qs(parsed.query)
        values = query.get("appId") or query.get("appid")
        if not values or not values[0].strip():
            raise ToolError("The URL does not contain an appId parameter")
        app_ref = values[0].strip()

    if not re.fullmatch(r"[A-Za-z0-9._-]+", app_ref):
        raise ToolError("The app ID contains unsupported characters")
    if country is None:
        raise ToolError("A standalone app ID requires --country")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ToolError("--country must be a two-letter ISO country code")
    return app_ref, country


def ssh_base(args: argparse.Namespace, *, tty: bool) -> list[str]:
    command = [
        "ssh",
        "-tt" if tty else "-T",
        "-p",
        str(args.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={args.timeout}",
    ]
    if args.identity_file:
        command.extend(["-i", str(args.identity_file.expanduser())])
    for option in args.ssh_option:
        command.extend(["-o", option])
    command.append(args.tv)
    return command


def get_device_info(args: argparse.Namespace) -> dict[str, Any]:
    command = ssh_base(args, tty=True)
    command.extend(["luna-send", "-n", "1", SDX_URI, "{}"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.timeout + 5,
        )
    except FileNotFoundError as exc:
        raise ToolError("The ssh command was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("The TV did not respond over SSH") from exc

    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        reason = lines[-1] if lines else f"exit code {result.returncode}"
        raise ToolError(f"Could not query the TV: {reason}")
    response = parse_json(result.stdout.replace("\r", ""))
    if not isinstance(response, dict) or not response.get("returnValue"):
        raise ToolError("The SDX service did not return valid headers")
    return response


def set_header(headers: dict[str, str], name: str, value: str) -> None:
    for existing in list(headers):
        if existing.casefold() == name.casefold():
            del headers[existing]
    headers[name] = value


def make_device_headers(device: dict[str, Any], country: str) -> dict[str, str]:
    headers = {
        key: str(value)
        for key, value in device.items()
        if key.casefold().startswith("x-") and isinstance(value, (str, int, float))
    }
    cookie = device.get("cookie")
    if isinstance(cookie, str) and cookie:
        headers["X-Cookie"] = cookie

    country_headers = {
        "x-country",
        "x-country-code",
        "x-device-country",
        "x-device-country-code",
    }
    for name in list(headers):
        if name.casefold() in country_headers:
            headers[name] = country
    set_header(headers, "X-Country-Code", country)
    set_header(headers, "Accept", "application/json")
    if not any(name.casefold() == "x-authentication" for name in headers):
        raise ToolError("The TV did not return X-Authentication")
    return headers


def curl_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\r", "").replace("\n", "")
    return f'"{escaped}"'


def request_from_tv(
    args: argparse.Namespace,
    url: str,
    headers: dict[str, str],
    data: dict[str, str],
) -> Any:
    config = [
        f"url = {curl_quote(url)}",
        "silent",
        "show-error",
        "location",
        "fail-with-body",
        f"max-time = {args.timeout}",
        "request = POST",
        f"data = {curl_quote(urllib.parse.urlencode(data))}",
    ]
    for name, value in headers.items():
        config.append(f"header = {curl_quote(f'{name}: {value}')}")

    command = ssh_base(args, tty=False)
    command.extend(["curl", "--config", "-"])
    try:
        result = subprocess.run(
            command,
            input=("\n".join(config) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=args.timeout + 10,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("The regional initialization request timed out") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"The regional request from the TV failed: {detail}")
    return parse_json(result.stdout.decode("utf-8", errors="replace"))


def create_regional_session(
    args: argparse.Namespace, headers: dict[str, str], country: str
) -> str:
    response = request_from_tv(
        args,
        f"https://{country}.lgtvsdp.com/rest/sdp/v11.2/initservices",
        headers,
        {"api_info": "auth:Y,provisioning:N,services:N,appupdatecheck:N"},
    )
    session = find_value(response, {"sessionID", "sessionId", "session_id"})
    if not isinstance(session, str) or not session:
        message = find_value(response, {"message", "errorMessage", "error"})
        raise ToolError(f"LG did not return a regional session: {message or 'no details'}")
    return session


def http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    timeout: int,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded; charset=UTF-8"
        )
    request = urllib.request.Request(url, data=body, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_json(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            message = find_value(
                parse_json(payload), {"message", "errorMessage", "error"}
            )
        except ToolError:
            message = None
        raise ToolError(
            f"HTTP {exc.code} while querying {url}: {message or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Could not connect to {url}: {exc.reason}") from exc


def get_public_metadata(app_no: str, country: str, timeout: int) -> dict[str, str]:
    response = http_json(
        f"https://{country.casefold()}.lgappstv.com/api/tvapp/retrieveAppInfo.ajax",
        headers={"Referer": f"https://{country.casefold()}.lgappstv.com/"},
        data={"appId": app_no},
        timeout=timeout,
    )
    products = response.get("prodInfo") if isinstance(response, dict) else None
    if not isinstance(products, list) or not products or not isinstance(products[0], dict):
        raise ToolError(f"No metadata was found for app ID {app_no} in {country}")
    product = products[0]
    result: dict[str, str] = {}
    for source, target in (
        ("appName", "name"),
        ("appVer", "version"),
        ("catCode", "category"),
    ):
        value = product.get(source)
        if value not in (None, ""):
            result[target] = html.unescape(str(value)).strip()
    if not result.get("name") or not result.get("category"):
        raise ToolError("The metadata does not include a name and category")
    return result


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char.casefold() for char in decomposed if char.isalnum())


def collect_apps(payload: Any) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in walk(payload):
        if not isinstance(node, dict):
            continue
        app_id = next(
            (
                node[key]
                for key in ("id", "app_id", "appId")
                if isinstance(node.get(key), str) and node[key]
            ),
            None,
        )
        name = next(
            (
                node[key]
                for key in ("name", "title", "app_name", "appName")
                if isinstance(node.get(key), str) and node[key]
            ),
            None,
        )
        if app_id and name and (app_id, name) not in seen:
            seen.add((app_id, name))
            result.append((app_id, name))
    return result


def resolve_app_id(
    metadata: dict[str, str],
    country: str,
    headers: dict[str, str],
    timeout: int,
    verbose: bool,
) -> str:
    wanted = normalize_name(metadata["name"])
    category = metadata["category"]
    base = f"https://{country}.tvsdp.lgeapi.com/lgstore/7.0"
    seen: set[str] = set()
    partial: list[tuple[str, str]] = []
    page_size = 500

    for page in range(20):
        query = urllib.parse.urlencode(
            {
                "callback": "response",
                "category_id": category,
                "item_cnt": page_size,
                "rank_type": 5,
                "start_idx": 1 + page * page_size,
                "index": page,
            }
        )
        payload = http_json(
            f"{base}/discovery/item/GAMESAPPS?{query}",
            headers=headers,
            timeout=timeout,
        )
        candidates = collect_apps(payload)
        new_count = 0
        for app_id, name in candidates:
            if app_id in seen:
                continue
            seen.add(app_id)
            new_count += 1
            normalized = normalize_name(name)
            if normalized == wanted:
                return app_id
            if wanted in normalized or normalized in wanted:
                partial.append((app_id, name))
        log(f"Page {page + 1}: {new_count} new applications", verbose)
        if not candidates or new_count == 0 or len(candidates) < page_size:
            break

    if len(partial) == 1:
        return partial[0][0]
    if partial:
        choices = ", ".join(f"{name} ({app_id})" for app_id, name in partial[:5])
        raise ToolError(f"Several possible matches were found: {choices}")
    raise ToolError(f"Could not find {metadata['name']!r} in category {category}")


def store_request(
    country: str,
    path: str,
    app_id: str,
    headers: dict[str, str],
    timeout: int,
    *,
    stub: bool = False,
) -> Any:
    params: dict[str, str] = {"app_id": app_id}
    if stub:
        params["stub"] = "N"
    query = urllib.parse.urlencode(params)
    url = f"https://{country}.tvsdp.lgeapi.com/lgstore/7.0/{path}?{query}"
    response = http_json(url, headers=headers, timeout=timeout)
    message = find_value(response, {"errorMessage"})
    if message:
        raise ToolError(str(message))
    return response


def safe_name(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return result or "application"


def choose_output(path: Path | None, app_id: str, version: str | None) -> Path:
    name = safe_name(app_id)
    if version:
        name += "_" + safe_name(version)
    name += "_all.ipk"
    if path is None:
        return Path.cwd() / name
    expanded = path.expanduser()
    if expanded.exists() and expanded.is_dir():
        return expanded / name
    return expanded


def as_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def download(
    url: str,
    output: Path,
    expected_size: int | None,
    timeout: int,
    force: bool,
) -> tuple[int, str]:
    output = output.resolve()
    if output.exists() and not force:
        raise ToolError(f"The file already exists: {output}. Use --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    if partial.exists():
        partial.unlink()

    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open(
            "wb"
        ) as destination:
            while block := response.read(1024 * 1024):
                destination.write(block)
                digest.update(block)
                size += len(block)
        with partial.open("rb") as downloaded:
            if downloaded.read(8) != b"!<arch>\n":
                raise ToolError("The server did not return a valid IPK package")
        if expected_size is not None and size != expected_size:
            raise ToolError(
                f"Unexpected size: expected {expected_size} bytes, received {size}"
            )
        partial.replace(output)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise
    return size, digest.hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Download an IPK from LG Content Store without changing the TV region."
    )
    result.add_argument(
        "target", help="lgappstv.com URL, numeric app ID, or internal app ID"
    )
    result.add_argument(
        "--tv",
        "-t",
        required=True,
        metavar="USER@HOST",
        help="TV SSH destination",
    )
    result.add_argument(
        "--country",
        "-c",
        metavar="XX",
        help="Store country. Required for a standalone app ID",
    )
    result.add_argument("--ssh-port", "-p", type=int, default=22, help="SSH port")
    result.add_argument(
        "--identity-file", "-i", type=Path, help="SSH private key file"
    )
    result.add_argument(
        "--ssh-option",
        "-s",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional ssh option. May be specified more than once",
    )
    result.add_argument(
        "--app-key",
        "-a",
        default=DEFAULT_APP_KEY,
        help="LG Content Store public client key",
    )
    result.add_argument(
        "--output", "-o", type=Path, help="Output file or existing directory"
    )
    result.add_argument(
        "--timeout",
        "-T",
        type=int,
        default=30,
        help="Timeout in seconds",
    )
    result.add_argument(
        "--force", "-f", action="store_true", help="Replace an existing output file"
    )
    result.add_argument(
        "--verbose", "-v", action="store_true", help="Show progress information"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not 1 <= args.ssh_port <= 65535:
            raise ToolError("--ssh-port must be between 1 and 65535")
        if args.timeout <= 0:
            raise ToolError("--timeout must be greater than zero")
        app_ref, country = parse_target(args.target, args.country)

        log(f"Querying TV {args.tv}", args.verbose)
        device = get_device_info(args)
        bootstrap = make_device_headers(device, country)
        log(f"Creating a temporary store session for {country}", args.verbose)
        session = create_regional_session(args, bootstrap, country)
        headers = dict(bootstrap)
        set_header(headers, "X-Authentication", session)
        set_header(headers, "X-Lge-AppKey", args.app_key)
        set_header(headers, "Accept", "application/json, text/javascript, */*; q=0.01")

        metadata: dict[str, str] = {}
        if app_ref.isdigit():
            metadata = get_public_metadata(app_ref, country, args.timeout)
            log(f"Resolving the internal ID for {metadata['name']}", args.verbose)
            app_id = resolve_app_id(
                metadata, country, headers, args.timeout, args.verbose
            )
        else:
            app_id = app_ref

        detail = store_request(
            country,
            "discovery/item/GAMESAPPS/Detail",
            app_id,
            headers,
            args.timeout,
        )
        install = store_request(
            country,
            "discovery/item/GAMESAPPS/Install",
            app_id,
            headers,
            args.timeout,
            stub=True,
        )
        file_url = find_value(install, {"fileURL", "fileUrl", "file_url"})
        if not isinstance(file_url, str) or not file_url.startswith(("http://", "https://")):
            raise ToolError("The LG response does not contain fileURL")

        version_value = metadata.get("version") or find_value(detail, {"version", "appVer"})
        version = str(version_value) if version_value not in (None, "") else None
        expected_size = as_int(
            find_value(install, {"packFileSize", "packedSize", "fileSize"})
        )
        output = choose_output(args.output, app_id, version)
        log(f"Downloading {output.name}", args.verbose)
        size, checksum = download(
            file_url, output, expected_size, args.timeout, args.force
        )

        print(f"File: {output.resolve()}")
        print(f"Application: {metadata.get('name', app_id)} ({app_id})")
        if version:
            print(f"Catalog version: {version}")
        print(f"Country: {country}")
        print(f"Size: {size} bytes")
        print(f"SHA-256: {checksum}")
        return 0
    except ToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
