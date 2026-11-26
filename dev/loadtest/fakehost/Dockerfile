FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      openssh-server python3 python3-apt sudo cron iproute2 \
 && rm -f /etc/ssh/ssh_host_*
RUN useradd -m -s /bin/bash -p '*' unbork \
 && echo 'unbork ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/unbork \
 && chmod 440 /etc/sudoers.d/unbork \
 && install -d -m 700 -o unbork -g unbork /home/unbork/.ssh
COPY --chown=unbork:unbork --chmod=600 .state/id_ed25519.pub /home/unbork/.ssh/authorized_keys
COPY --chmod=755 fakehost/entrypoint.sh /entrypoint.sh
RUN printf 'PasswordAuthentication no\nUsePAM no\nUseDNS no\nMaxStartups 100:30:200\n' > /etc/ssh/sshd_config.d/loadtest.conf \
 && mkdir -p /run/sshd
ENTRYPOINT ["/entrypoint.sh"]
