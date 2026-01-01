{
  description = "Development environment for pulsar-aws-lab load testing framework";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, utils, ... }:
    utils.lib.eachDefaultSystem (system:
      let
        pythonVersion = "3.13.11";

        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        # Platform-specific packages
        platformPkgs = if pkgs.stdenv.isDarwin then [
          pkgs.apple-sdk_15
        ] else [];
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Python tooling
            uv

            # Kubernetes/Cloud tools
            kubectl
            kubernetes-helm
            awscli2

            # Development utilities
            git
            jq
            yq
            ripgrep
          ] ++ platformPkgs;

          shellHook = ''
            # Clean pyenv from PATH to avoid conflicts
            export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v -e '/pyenv-virtualenv.*shims' -e '/.pyenv/shims' | tr '\n' ':' | sed 's/:*$//')
            unset PYTHONPATH

            # Ensure .python-version exists
            if [ ! -f .python-version ]; then
              echo "${pythonVersion}" > .python-version
            fi

            # Configure uv
            export UV_PYTHON_INSTALL_DIR=$PWD/.direnv/share/uv/python
            export UV_PYTHON=${pythonVersion}
            export UV_CACHE_DIR=$PWD/.direnv/uv

            # Ensure Python is installed via uv
            if ! ${pkgs.uv}/bin/uv python list 2>/dev/null | grep -q "${pythonVersion}"; then
              echo "Installing Python ${pythonVersion} via uv..."
              ${pkgs.uv}/bin/uv python install ${pythonVersion}
            fi

            # Create venv if needed or if Python version changed
            if ! rg -q "version_info = ${pythonVersion}" .venv/pyvenv.cfg 2>/dev/null; then
              echo "Setting up Python ${pythonVersion} virtual environment..."
              rm -rf .venv
              ${pkgs.uv}/bin/uv venv --python ${pythonVersion} --seed .venv
            fi

            # Activate virtualenv
            source .venv/bin/activate

            # Sync dependencies if pyproject.toml exists
            if [ -f pyproject.toml ]; then
              ${pkgs.uv}/bin/uv sync --quiet 2>/dev/null || true
            fi

            echo "Development environment ready. Python $(python --version 2>&1 | cut -d' ' -f2) at $VIRTUAL_ENV"
          '';
        };
      }
    );
}
