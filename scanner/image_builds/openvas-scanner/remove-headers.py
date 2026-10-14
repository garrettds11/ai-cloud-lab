"""Remove reviewed development headers; reject runtime dependency removals."""
import os
import subprocess

HEADERS = {
    "comerr-dev", "krb5-multidev", "libbrotli-dev", "libc-dev-bin", "libc6-dev",
    "libcrypt-dev", "libcurl4-gnutls-dev", "libgmp-dev", "libgnutls28-dev",
    "libidn2-dev", "libkrb5-dev", "libldap-dev", "libnghttp2-dev", "libnghttp3-dev",
    "libngtcp2-crypto-gnutls-dev", "libngtcp2-dev", "libp11-kit-dev", "libpsl-dev",
    "librtmp-dev", "libssh2-1-dev", "libssl-dev", "libtasn1-6-dev", "libzstd-dev",
    "linux-libc-dev", "nettle-dev", "zlib1g-dev",
}


def planned_removals(output):
    removed = {line.split()[1].split(":", 1)[0] for line in output.splitlines()
               if line.startswith(("Remv ", "Purg "))}
    unexpected = removed - HEADERS
    if unexpected:
        raise RuntimeError("Refusing runtime removal: " + ", ".join(sorted(unexpected)))
    return removed


def main():
    environment = dict(os.environ, LC_ALL="C", DEBIAN_FRONTEND="noninteractive")
    inventory = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package} ${db:Status-Status}\n"], text=True, env=environment)
    installed = {line.split()[0] for line in inventory.splitlines() if line.endswith(" installed")}
    selected = sorted(installed & HEADERS)
    if not selected:
        return
    command = ["apt-get", "--no-auto-remove", "purge", *selected]
    simulation = subprocess.check_output(command[:1] + ["--simulate"] + command[1:],
                                         text=True, env=environment)
    removed = planned_removals(simulation)
    if not set(selected).issubset(removed):
        raise RuntimeError("Header removal incomplete")
    subprocess.run(command[:1] + ["-y"] + command[1:], check=True, env=environment)
    print("Removed development headers:", ", ".join(sorted(removed)))


if __name__ == "__main__":
    main()
