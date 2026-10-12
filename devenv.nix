{ pkgs, ... }:
{
  languages.python.enable = true;
  languages.python.version = "3.12";
  languages.javascript.enable = true;
  languages.javascript.package = pkgs.nodejs_22;
  packages = with pkgs; [ uv hcloud openssh rclone curl jq sqlite ];
  enterShell = ''
    export PATH="$PWD/node_modules/.bin:$PATH"
  '';
}
