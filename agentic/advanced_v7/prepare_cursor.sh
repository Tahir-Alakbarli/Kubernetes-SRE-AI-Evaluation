#!/usr/bin/env bash
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
TRIAL="$HOME/micro1-v7-trials/t06-cursor-01"
USER_NAME="cursoragent"
cd "$PROJECT"
python3 agentic/advanced_v7/runner.py setup --trial "$TRIAL"
if ! id "$USER_NAME" >/dev/null 2>&1; then
    sudo useradd -m -s /bin/bash "$USER_NAME"
fi
sudo install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.kube"
kind get kubeconfig --name micro1-eval | sudo tee "/home/$USER_NAME/.kube/config" >/dev/null
sudo chown "$USER_NAME:$USER_NAME" "/home/$USER_NAME/.kube/config"
sudo chmod 600 "/home/$USER_NAME/.kube/config"
sudo install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/t06-01/runbooks"
for name in ticket.md README.md; do
    sudo install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$TRIAL/$name" "/home/$USER_NAME/t06-01/$name"
done
sudo install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$TRIAL/runbooks/operational-policy.md" "/home/$USER_NAME/t06-01/runbooks/operational-policy.md"
sudo chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME/t06-01"
chmod 700 "$HOME/Projects/micro1-hackathon"
sudo -u "$USER_NAME" -H env KUBECONFIG="/home/$USER_NAME/.kube/config" kubectl get nodes
printf '\nREADY: start Cursor using sudo -iu %s; export KUBECONFIG=/home/%s/.kube/config; cd ~/t06-01\n' "$USER_NAME" "$USER_NAME"
