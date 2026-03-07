{
  description = "desloppify - codebase health scanner";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python313;
        in
        {
          default = python.pkgs.buildPythonApplication {
            pname = "desloppify";
            version = "0.9.1";
            src = ./.;
            format = "pyproject";

            nativeBuildInputs = [ python.pkgs.setuptools ];

            propagatedBuildInputs = with python.pkgs; [
              tree-sitter-language-pack
              bandit
              defusedxml
              pillow
            ];

            doCheck = false;
          };
        }
      );

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python313;
        in
        {
          default = pkgs.mkShell {
            packages = [
              python
              python.pkgs.pip
              python.pkgs.setuptools
            ];
          };
        }
      );
    };
}
