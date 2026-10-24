# webOS Store IPK Downloader

Download official IPK packages from regional LG Content Store / LG Apps servers through a rooted webOS TV, without changing the region configured on the TV.

The tool accepts either an `lgappstv.com` application URL, a public numeric application ID, or an internal store application ID. It asks the TV for its normal LG device headers, creates a temporary session for the requested store country, resolves the application, obtains an official signed download URL, and saves the IPK locally.

This project is not affiliated with or endorsed by LG Electronics.

## What it does

- Uses a rooted webOS TV as the authenticated LG device.
- Supports regional store URLs without changing the TV region.
- Resolves public numeric IDs to the internal IDs used by the TV store API.
- Downloads the package directly from the URL returned by LG.
- Verifies the expected package size.
- Checks that the downloaded file has a valid IPK archive header.
- Calculates and prints the SHA-256 checksum.
- Writes downloads atomically through a temporary `.part` file.

The script only downloads packages. It does not install applications, decrypt NCG content, register DRM rights, or bypass application authentication.

## Requirements

- Python 3.10 or newer.
- A rooted LG webOS TV.
- SSH access to the TV.
- Public-key authentication or a configured SSH agent.
- `luna-send` and `curl` available on the TV.
- The computer and TV must be able to reach each other over the network.
- Internet access from both the computer and the TV.

Before running the script, verify that SSH works without an interactive password prompt:

```bash
ssh root@192.168.1.80
```

The SSH user and address above are examples. Use the values for your own TV.

## Tested configuration

This tool was developed and tested with the following TV configuration:

- TV: LG OLED CX 55-inch, 2020 model series.
- webOS version: 5.6.2.
- Platform: W20O.
- Firmware version: 04.64.00.

Other LG webOS models and firmware versions may behave differently because the store APIs and system services used by the script are private and undocumented.

## Usage

```text
usage: webos_ipk.py [-h] --tv USER@HOST [--country XX]
                    [--ssh-port SSH_PORT]
                    [--identity-file IDENTITY_FILE]
                    [--ssh-option KEY=VALUE] [--app-key APP_KEY]
                    [--output OUTPUT] [--timeout TIMEOUT]
                    [--force] [--verbose]
                    target
```

### Options

| Option | Short | Description |
| --- | --- | --- |
| `--help` | `-h` | Show the help message. |
| `--tv USER@HOST` | `-t` | Required SSH destination for the TV. |
| `--country XX` | `-c` | Two-letter store country. Required for a standalone ID. |
| `--ssh-port PORT` | `-p` | SSH port. Defaults to `22`. |
| `--identity-file PATH` | `-i` | SSH private key file. |
| `--ssh-option KEY=VALUE` | `-s` | Additional SSH option. May be repeated. |
| `--app-key KEY` | `-a` | LG store client key. The known public store key is used by default. |
| `--output PATH` | `-o` | Output file or an existing output directory. |
| `--timeout SECONDS` | `-T` | Network and SSH timeout. Defaults to `30`. |
| `--force` | `-f` | Replace an existing output file. |
| `--verbose` | `-v` | Print progress information. |

`-T` is used for `--timeout` because `-t` is already assigned to `--tv`.

## Example using a store URL

The following fictional setup uses the real German LG Apps listing for YouTube, public application ID `95384`:

```bash
python3 webos_ipk.py \
  'https://de.lgappstv.com/main/tvapp/detail?appId=95384' \
  --tv root@192.168.1.80 \
  --identity-file ~/.ssh/id_ed25519 \
  --output ./youtube.ipk \
  --verbose
```

The country is derived from the `de.lgappstv.com` hostname, so `--country` is not required.

If the SSH key is already available through an SSH agent, omit `--identity-file`:

```bash
python3 webos_ipk.py \
  'https://de.lgappstv.com/main/tvapp/detail?appId=95384' \
  -t root@192.168.1.80 \
  -o ./youtube.ipk
```

## Example using a numeric ID

A standalone numeric ID does not contain country information, so the country must be supplied explicitly:

```bash
python3 webos_ipk.py 95384 \
  --country DE \
  --tv root@192.168.1.80 \
  --output ./youtube.ipk
```

An internal store ID can also be used, but it still requires `--country`.

## How it works

1. The input is parsed and the requested store country is determined from the URL or `--country`.
2. The script connects to the TV over SSH and calls:

   ```text
   luna://com.webos.service.sdx/getHttpHeaderForServiceRequest
   ```

3. The TV returns the device headers normally used by LG services. These include device metadata, an authentication value, and a store cookie.
4. Using those headers, `curl` runs on the TV and requests a temporary session from the regional `initservices` endpoint.
5. For a numeric ID, the public LG Apps metadata endpoint provides the application name and category. The regional TV API is then searched for the internal application ID.
6. The script requests application details and installation metadata from the regional `/lgstore/7.0` API.
7. LG returns a signed package URL and the expected package size.
8. The package is downloaded locally, checked, renamed atomically, and hashed with SHA-256.

The regional session is temporary. The script does not update the country stored in webOS settings.

## Security and privacy

The TV response contains sensitive values such as its device ID, LG authentication value, and cookies. The script:

- Keeps these values in memory.
- Sends the regional request configuration to remote `curl` through SSH standard input.
- Does not place authentication headers in command-line arguments.
- Does not print authentication headers, cookies, or the device ID.
- Does not write session data to disk.

Do not publish debug logs without reviewing them first. Never commit SSH private keys, downloaded proprietary IPKs, temporary signed URLs, cookies, or device headers.

## NCG and installation

Many official LG packages contain files encrypted with LG's NCG DRM. Downloading an IPK does not automatically grant the TV the rights required to run it.

Installing an encrypted official package with the development installer may place it under `/media/developer/apps`, where its encrypted entry point can fail to load. A normal store installation typically uses `/media/cryptofs/apps` and registers NCG rights through webOS system services.

This downloader intentionally does not automate that process.

## Known limitations

- LG store APIs used by the script are private and undocumented. They may change without notice.
- Behavior may differ between webOS versions and TV firmware releases.
- Application availability depends on country, model, platform version, and publisher restrictions.
- A public catalog version can differ from the version stored inside the downloaded IPK.
- The application may still perform its own account, subscription, location, or playback checks after installation.
- An IPK may be downloadable but unusable without valid NCG rights.

## Troubleshooting

### The TV does not respond over SSH

Confirm the address, SSH port, key, and root SSH configuration. Try connecting with `ssh` directly before using the script.

### A standalone ID is rejected

Add a two-letter country code:

```bash
python3 webos_ipk.py 95384 -c DE -t root@192.168.1.80
```

### The application cannot be found

Verify that the application is listed for the requested country. Availability can also depend on the TV model and webOS version.

### The output file already exists

Choose another path or pass `--force`:

```bash
python3 webos_ipk.py 95384 -c DE -t root@192.168.1.80 -f
```

### SSH needs extra options

Use `--ssh-option` more than once if required:

```bash
python3 webos_ipk.py 95384 \
  -c DE \
  -t root@tv.local \
  -s StrictHostKeyChecking=yes \
  -s UserKnownHostsFile=~/.ssh/known_hosts
```

## Responsible use

Use this tool only with TVs and applications you are authorized to access. Respect application licenses, publisher terms, regional availability, and applicable law. Do not redistribute downloaded proprietary packages without permission.
