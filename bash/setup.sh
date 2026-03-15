apt update && apt -y upgrade;
apt install rsync ncdu git nfs-kernel-server;
mkdir -p /usr/share/pac/;
cd /usr/share/pac/;
git clone https://github.com/pacmac/pullback.git
cd /usr/share/pac/pullback/bash
./nfs-share.sh /usr/share/pac;
grep -qxF 'export PATH="$PATH:/usr/share/pac/pullback/bash"' ~/.bashrc || echo 'export PATH="$PATH:/usr/share/pac/pullback/bash"' >> ~/.bashrc;
