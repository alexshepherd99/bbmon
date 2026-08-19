# Reaching the Pi

How to get an SSH session to the bbmon Pi from a development machine, and how
that access is secured. Written at M2, when `deploy.sh` first needed it.

**This file carries the procedure only.** No addresses, host key fingerprints,
hostnames, or anything else identifying a particular machine or network belong
in this repository — that is a deliberate limit, not an oversight. Those live
in `docs/pi-access.local.md`, which `.gitignore` excludes. If that file is
lost, its contents are not recoverable from the repository, and that is
accepted.

So: wherever a step below needs a real address or a fingerprint to compare
against, the local notes are where it comes from.

## From the Crostini container

The container reaches the Pi without special setup. It sits on a host-only
subnet and routes out through ChromeOS rather than sharing a link with the Pi,
which is why `plan.md` previously recorded — wrongly — that it could not reach
the Pi at all. Name resolution and routing both work unaided.

`deploy.sh` therefore runs from the container directly. It needs `rsync`, which
is not installed by default:

```sh
sudo apt install rsync
```

## First-time key setup

Do this once per development machine. It ends with password authentication
disabled on the Pi, so read the last step before starting.

Every step below that prompts — for the Pi's password, for a key passphrase, or
for sudo — needs a real interactive terminal. A session driven by an agent has
no TTY to prompt on, and the failure is quiet: SSH reports
`ssh_askpass: exec(/usr/bin/ssh-askpass): No such file or directory` and gives
up. Run these by hand; the rest of the setup can be driven remotely afterwards.

**1. Generate a key, if this machine has none.**

```sh
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname) -> bbmon pi"
```

Use a passphrase, and let `ssh-agent` hold it (`ssh-add`) so `deploy.sh` is not
prompted mid-deploy.

**2. Check the host key before trusting it.** On first connection SSH will show
a fingerprint. Compare it against the Pi's own console rather than accepting it
blind:

```sh
# On the Pi itself:
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Record the value in the local notes so a later change can be checked against
something. A fingerprint that changes when the Pi has *not* been reimaged is
worth stopping for.

**3. Copy the public key to the Pi.** This is the one time a password is used.

```sh
ssh-copy-id pi@<host>
```

**4. Confirm key-only login works.** `BatchMode=yes` makes SSH fail rather than
fall back to a password prompt, which is what proves the key is doing the work:

```sh
ssh -o BatchMode=yes pi@<host> true && echo "key auth OK"
```

**5. Add a shortcut** so `deploy.sh` and the commands here can use a short name.
In `~/.ssh/config`:

```
Host <name>
  HostName <address-or-name>
  User pi
  IdentityFile ~/.ssh/id_ed25519
```

`deploy.sh` defaults to the host in `$BBMON_HOST`, and takes one as an argument
otherwise.

**6. Disable password authentication on the Pi.** A guessable password on the
well-known `pi` account is the largest single hole on a stock Raspberry Pi, and
it has nothing to do with bbmon's own code — which is why `plan.md` treats this
as hole-closing rather than hardening.

**Keep your current SSH session open in another terminal while doing this.** If
the key turns out not to work, that open session is the only way back in
without a keyboard and monitor.

```sh
sudo tee /etc/ssh/sshd_config.d/10-bbmon-no-passwords.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

**The `10-` prefix is load-bearing, not decoration.** `sshd` takes the *first*
value it obtains for a keyword, not the last — the opposite of most config
systems. Raspberry Pi OS ships `/etc/ssh/sshd_config.d/50-cloud-init.conf`
containing `PasswordAuthentication yes`, so a drop-in numbered above 50 is read
second and silently loses. This was written as `60-` first and had no effect at
all: `sshd -T` still reported `passwordauthentication yes` while the file sat
there looking correct.

`sshd -t` validates the configuration before the restart applies it. If it
reports an error, fix it before restarting, or the Pi will be left unreachable.

Consider arming an automatic revert first, so a mistake repairs itself rather
than needing a keyboard and monitor attached to a headless Pi:

```sh
# Undoes the change in 5 minutes unless you disarm it.
sudo systemd-run --on-active=300 --unit=ssh-failsafe \
  /bin/sh -c "rm -f /etc/ssh/sshd_config.d/10-bbmon-no-passwords.conf; systemctl restart ssh"

# ...verify from a NEW terminal, then disarm:
sudo systemctl stop ssh-failsafe.timer
```

**7. Verify it took.** From a *new* terminal:

```sh
ssh -o PreferredAuthentications=password pi@<host>
```

This must be refused. If it asks for a password, step 6 did not take effect.

## Checking the current state

```sh
# Which authentication methods does the Pi offer?
ssh -o PreferredAuthentications=none -o PubkeyAuthentication=no pi@<host> 2>&1 \
  | grep -i 'permission denied'
```

The listed methods should be `publickey` alone. While `password` still appears
there, step 6 has not taken effect — regardless of what the drop-in file says.

Ask the Pi what it actually resolved, which is the answer that counts:

```sh
sudo sshd -T | grep -iE '^(passwordauthentication|kbdinteractiveauthentication)'
```

## What the deploy scripts may do as root

`deploy.sh` runs over a non-interactive SSH session, where a sudo password
prompt has no terminal to appear on: it fails the deploy rather than asking for
anything. `bootstrap.sh` therefore installs `/etc/sudoers.d/bbmon-deploy`,
granting the admin account passwordless sudo for exactly two things — restarting
`bbmon-pinger`, `bbmon-speedtest` and `bbmon-web`, and writing
`/var/lib/bbmon/build-stamp`. Nothing else, and specifically not `bbmon-init` or
either half of the reboot mechanism.

This is not the same as the account having no other privilege: the admin user is
in the `sudo` group like any Debian administrator, and can still do anything
*with* a password. What the rule limits is what happens without one.

To check it, drop any cached credential first:

```sh
sudo -k
sudo -n systemctl restart bbmon-web      # should work
sudo -n systemctl restart bbmon-init     # should be refused
```

**The `sudo -k` is the whole test.** `timestamp_type=global` is set on Raspberry
Pi OS, so a sudo password typed in any session authorises every other session
for a few minutes — including one an automated deploy is running in. Without
`sudo -k` first, both commands above succeed and prove nothing at all, which is
exactly what happened when this rule was first checked on 2026-08-19.

## When the address moves

If the Pi takes its address by DHCP, resolving it by name is what makes that
survivable. If the name stops resolving, find it on the local network by its
Raspberry Pi Foundation MAC prefix:

```sh
ip neigh | grep -Ei 'b8:27:eb|dc:a6:32|e4:5f:01|2c:cf:67'
```

Then deploy to the address directly:

```sh
scripts/deploy.sh pi@<address>
```

A DHCP reservation on the router is the durable fix, but that is router
configuration rather than repo configuration, so it is not scripted here.

## What the Pi does not have

- **No credentials for this repository.** It is public, so `update.sh` pulls
  over HTTPS with nothing stored. Nothing on the Pi can push.
- **No password login**, after step 6.
- **No login for the service account.** `bbmon` is created with
  `--shell /usr/sbin/nologin` and exists only to own the services and their
  data directory.
- **No blanket passwordless sudo.** Earlier Raspberry Pi OS images gave the
  admin account exactly that, and `deploy.sh` depended on it without saying so;
  current images do not. The narrow replacement is the section above.
