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
- `luna-send` available on the TV. The default regional initialization method also needs `curl` on the TV.
- The computer and TV must be able to reach each other over the network.
- Internet access from the computer. By default the TV also needs access to the regional initialization endpoint.

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
                    [--force] [--init-from-computer]
                    [--ca-bundle PATH | --insecure] [--verbose]
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
| `--init-from-computer` | | Send the regional initialization request from the computer when the TV cannot reach LG. Device headers still come from the TV over SSH. |
| `--ca-bundle PATH` | | Use a specified CA certificate bundle for HTTPS requests from the computer. |
| `--insecure` | | Disable HTTPS certificate verification for computer requests and the TV's regional request. Use only as a last resort. |
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
4. Using those headers, `curl` runs on the TV and requests a temporary session from the regional `initservices` endpoint. With `--init-from-computer`, Python makes this request from the computer instead.
5. For a numeric ID, the public LG Apps metadata endpoint provides the application name and category. The regional TV API is then searched for the internal application ID.
6. The script requests application details and installation metadata from the regional `/lgstore/7.0` API.
7. LG returns a signed package URL and the expected package size.
8. The package is downloaded locally, checked, renamed atomically, and hashed with SHA-256.

The regional session is temporary. The script does not update the country stored in webOS settings.

## Running the tests

`test_webos_ipk.py` contains the regression tests for regional initialization and HTTPS certificate handling. It checks that initialization can run on the computer or the TV, that custom CA bundles and `--insecure` configure TLS as requested, and that certificate failures produce useful errors. The tests mock network and SSH calls, so they do not require a TV or download an IPK.

Run them with Python's standard library test runner:

```bash
python3 -B -m unittest -v
```

## Security and privacy

The TV response contains sensitive values such as its device ID, LG authentication value, and cookies. The script:

- Keeps these values in memory.
- Sends the regional request configuration to remote `curl` through SSH standard input.
- With `--init-from-computer`, sends the same device headers directly to LG over HTTPS from the computer.
- Does not place authentication headers in command-line arguments.
- Does not print authentication headers, cookies, or the device ID.
- Does not write session data to disk.

Do not publish debug logs without reviewing them first. Never commit SSH private keys, downloaded proprietary IPKs, temporary signed URLs, cookies, or device headers.

## Installing a downloaded IPK

There is no single installation method that works for every IPK. Choose the method according to the package type:

| Package type | Recommended method | Expected result |
| --- | --- | --- |
| Your own webOS app | `ares-install` or webOS Dev Manager | Normally works through Developer Mode. |
| Homebrew or another unencrypted community package | Homebrew Channel, webOS Dev Manager, `ares-install`, or the Luna command below | Normally works on a compatible TV. |
| Official LG Content Store package without protected content | A development installer may work, but this is not guaranteed | Try the methods below and test the application. |
| Official LG Content Store package protected with NCG | Install it from LG Content Store in a supported region | Sideloading may report success, but the application can still fail to launch. |

webOS Dev Manager, `ares-install`, and the direct Luna command are different front ends for the development installation flow. They are useful alternatives when a connection or client tool fails, but they do not bypass NCG protection.

Only install IPKs from sources you trust. An IPK can contain application code and services that run on the TV.

### Option 1: webOS Dev Manager

[webOS Dev Manager](https://github.com/webosbrew/dev-manager-desktop) provides a graphical interface and supports both Developer Mode TVs and rooted TVs. Add the TV, connect to it, and use the application installation action to select the downloaded IPK.

This is usually the easiest option for Homebrew packages. On a rooted TV, configure the connection with the TV address, SSH port `22`, and the `root` user. Do not share the private key or password used for that connection.

### Option 2: official webOS CLI

Install LG's current command-line tools:

```bash
npm install -g @webos-tools/cli
```

Configure the TV with `ares-setup-device`, then install and verify the package:

```bash
ares-setup-device
ares-install --device myTV ./com.example.app_1.0.0_all.ipk
ares-install --device myTV --list
ares-launch --device myTV com.example.app
```

On a non-rooted TV this requires the LG Developer Mode app, its SSH key, and an active Developer Mode session. Apps installed this way are development apps and may be removed when Developer Mode expires.

### Option 3: install directly over root SSH

On a rooted TV, copy the package to a temporary location:

```bash
scp -O ./com.example.app_1.0.0_all.ipk root@192.168.1.80:/tmp/app.ipk
ssh root@192.168.1.80
```

Then run this command on the TV:

```bash
luna-send-pub -i -f \
  luna://com.webos.appInstallService/dev/install \
  '{"id":"com.ares.defaultName","ipkUrl":"/tmp/app.ipk","subscribe":true}'
```

Wait for the subscription output to report that installation has finished. The value passed as `id` above is the conventional placeholder used by development installers; the actual application ID is read from the package.

This direct method uses the same development installer as `ares-install`. It is useful for diagnosing client-side problems, but it does not make a protected store package behave like a normal Content Store installation.

Do not use `opkg install` for these files. Although both use the `.ipk` extension, webOS application packages must be registered through the application installer. Manually extracting or copying an application directory also skips registration and is not a complete installation.

### Option 4: Homebrew Channel

If the application is available in [Homebrew Channel](https://github.com/webosbrew/webos-homebrew-channel), installing it there is preferable to manually sideloading its IPK. Homebrew Channel manages its catalog, package hashes, installation, and updates. It ultimately uses the development installer for ordinary Homebrew packages.

### Official store packages and NCG

Many official LG packages contain files encrypted with LG's NCG protection. Downloading the IPK does not grant the TV the rights required to decrypt and run those files.

A development installation normally places an app under `/media/developer/apps`. Content Store apps are normally installed under `/media/cryptofs/apps`, with the required rights handled by LG's store and DRM services. Consequently, an encrypted IPK can be accepted by `ares-install` or `dev/install` and appear in the launcher, but still show a blank screen, immediately close, or fail to load its entry point.

There is no supported command that turns a downloaded protected IPK into a licensed Content Store installation. The reliable method for such a package is installing it through LG Content Store on a supported TV and region. This downloader intentionally does not decrypt content, obtain DRM keys, or reproduce the protected store installation flow.

### What to try when an installed app does not work

1. Confirm that the package was built for your TV's webOS version and architecture.
2. Check whether the application appears in `ares-install --device myTV --list`.
3. Launch it from the command line with `ares-launch` and inspect the returned error.
4. If it is a Homebrew package, try Homebrew Channel or webOS Dev Manager instead of a manual command.
5. If it is an official store package, assume NCG may be involved even when installation reported success.
6. If the app opens but displays a network error, verify DNS, TLS certificates, the TV clock, and whether the application's own service is restricted by account or region. This is different from an IPK installation failure.

The commands above follow the [LG webOS CLI developer guide](https://webostv.developer.lge.com/develop/tools/cli-dev-guide), the [LG Developer Mode documentation](https://webostv.developer.lge.com/develop/getting-started/developer-mode-app), and the [webOS Homebrew installation guidance](https://www.webosbrew.org/pages/commands-cheatsheet).

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

### The TV cannot reach the regional initialization endpoint

If the regional request fails with a timeout or a network error while SSH and the TV's Content Store still work, create the session from the computer:

```bash
python3 webos_ipk.py \
  'https://de.lgappstv.com/main/tvapp/detail?appId=95384' \
  -t root@192.168.1.80 \
  --init-from-computer
```

### macOS reports a certificate verification failure

The Python installation may be missing a usable CA certificate bundle. Check the computer's date and certificate setup first. If you have a trusted PEM bundle, pass it with `--ca-bundle /path/to/cacert.pem`. This applies to the regional request, catalog queries, and package download made by the computer.

For the case where the TV cannot reach the regional endpoint and Python needs a CA bundle, use both options:

```bash
python3 webos_ipk.py \
  'https://de.lgappstv.com/main/tvapp/detail?appId=95384' \
  -t root@192.168.1.80 \
  --init-from-computer \
  --ca-bundle /path/to/cacert.pem
```

If you need to proceed while investigating the certificate problem, `--insecure` disables HTTPS certificate verification and prints a warning. It also affects the TV's regional request when that method is used. This exposes device authentication headers and package downloads to interception, so avoid using it on untrusted networks.

## Responsible use

Use this tool only with TVs and applications you are authorized to access. Respect application licenses, publisher terms, regional availability, and applicable law. Do not redistribute downloaded proprietary packages without permission.
