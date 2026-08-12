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
sudo tee /etc/ssh/sshd_config.d/60-bbmon-no-passwords.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

`sshd -t` validates the configuration before the restart applies it. If it
reports an error, fix it before restarting, or the Pi will be left unreachable.

**7. Verify it took.** From a *new* terminal:

```sh
ssh -o PreferredAuthentications=password pi@<host>
```

This must be refused. If it asks for a password, step 6 did not take effect.

## Checking the current state

```sh
# Which authentication methods does the Pi offer?
ssh -o PreferredAuthentications=none pi@<host> 2>&1 | grep -i 'permission denied'
```

The listed methods should be `publickey` alone. While `password` still appears
there, step 6 has not been done.

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
