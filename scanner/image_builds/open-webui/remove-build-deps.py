"""Remove native build packages only; reject any proposed runtime removal."""
import os
import subprocess


# This candidate installs binary wheels and does not compile extensions at runtime.
# Keep runtime libraries (including libgcc-s1, libstdc++6 and libmariadb3).
BUILD_PACKAGES = {
    "build-essential", "dpkg-dev", "make", "gcc", "gcc-12", "g++", "g++-12",
    "libgcc-12-dev", "libstdc++-12-dev", "libc6-dev", "linux-libc-dev",
    "libcrypt-dev", "libnsl-dev", "libtirpc-dev", "libmariadb-dev",
    "libmariadb-dev-compat", "libssl-dev", "zlib1g-dev",
    "binutils", "binutils-common", "binutils-x86-64-linux-gnu",
}


def planned_removals(output):
    removed = {line.split()[1].split(":", 1)[0] for line in output.splitlines()
               if line.startswith(("Remv ", "Purg "))}
    unexpected = removed - BUILD_PACKAGES
    if unexpected:
        raise RuntimeError("Refusing to remove runtime packages: " + ", ".join(sorted(unexpected)))
    return removed


def main():
    environment = dict(os.environ, LC_ALL="C", DEBIAN_FRONTEND="noninteractive")
    inventory = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package} ${db:Status-Status}\n"], text=True, env=environment)
    installed = {line.split()[0] for line in inventory.splitlines()
                 if line.endswith(" installed")}
    selected = sorted(installed & BUILD_PACKAGES)
    if not selected:
        return
    command = ["apt-get", "--no-auto-remove", "purge", *selected]
    simulation = subprocess.check_output(command[:1] + ["--simulate"] + command[1:],
                                         text=True, env=environment)
    removed = planned_removals(simulation)
    if not set(selected).issubset(removed):
        raise RuntimeError("Native build package removal was incomplete")
    subprocess.run(command[:1] + ["-y"] + command[1:], check=True, env=environment)
    print("Removed native build packages:", ", ".join(sorted(removed)))


if __name__ == "__main__":
    main()
