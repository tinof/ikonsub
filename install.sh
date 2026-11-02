#!/bin/bash
# Enable error handling
set -e

function install_provider() {
    local provider=$1
    local api_key_var_name=$2
    local extra_name=$3
    local script_name=$4
    local set_as_default=$5

    read -p "Enter your $provider API Key (optional): " api_key

    # Only update .env if user entered a new API key
    if [ -n "$api_key" ]; then
        if [ -f ".env" ]; then
            sed -i.bak "/^${api_key_var_name}_API_KEY=/d" .env
            rm -f .env.bak
        fi
        echo "${api_key_var_name}_API_KEY=$api_key" >> .env
    fi

    # Set as default provider if requested
    if [ "$set_as_default" = "set_default" ]; then
        if [ -f ".env" ]; then
            sed -i.bak "/^PROVIDER=/d" .env
            rm -f .env.bak
        fi
        echo "PROVIDER=$provider" >> .env
    fi

    if [ -n "$extra_name" ]; then
        extras+=("$extra_name")
    fi
    scripts_to_generate+=("$script_name")
}

if [ ! -d "scripts" ]; then
    echo "Please run the script from the root directory of the project."
    exit 1
fi

echo "Checking if Python 3 is installed..."
if ! python3 --version &>/dev/null; then
    echo "Python 3 not found. Please install Python 3 and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
MIN_VERSION="3.11.0"

if [[ "$(printf '%s\n' "$MIN_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$MIN_VERSION" ]]; then
    echo "Detected Python version is less than 3.11.0. Please upgrade your Python version."
    exit 1
else
    echo "Python version is compatible."
fi

echo "Checking if \"envsubtrans\" folder exists..."
if [ -d "envsubtrans" ]; then
    echo "\"envsubtrans\" folder already exists."
    read -p "Do you want to perform a clean install? This will delete the existing environment. (Y/N): " user_choice
    if [ "$user_choice" = "Y" ] || [ "$user_choice" = "y" ]; then
        echo "Performing a clean install..."
        rm -rf envsubtrans
        [ -f .env ] && rm .env
    elif [ "$user_choice" != "N" ] && [ "$user_choice" != "n" ]; then
        echo "Invalid choice. Exiting installation."
        exit 1
    fi
fi

python3 -m venv envsubtrans
source envsubtrans/bin/activate

extras=()
scripts_to_generate=("batch-translate")

echo "Installing command line modules..."

echo "Select providers to install:"
echo "0 = None"
echo "1 = OpenAI"
echo "2 = Google Gemini"
echo "3 = Anthropic Claude"
echo "a = All"
read -p "Enter your choice (0/1/2/3/a): " provider_choice

case $provider_choice in
    0)
        echo "No additional provider selected."
        ;;
    1)
        install_provider "OpenAI" "OPENAI" "openai" "gpt-subtrans" "set_default"
        ;;
    2)
        install_provider "Google Gemini" "GEMINI" "gemini" "gemini-subtrans" "set_default"
        ;;
    3)
        install_provider "Claude" "CLAUDE" "claude" "claude-subtrans" "set_default"
        ;;
    a)
        install_provider "Google Gemini" "GEMINI" "gemini" "gemini-subtrans" ""
        install_provider "OpenAI" "OPENAI" "openai" "gpt-subtrans" ""
        install_provider "Claude" "CLAUDE" "claude" "claude-subtrans" ""
        ;;
    *)
        echo "Invalid choice. Exiting installation."
        exit 1
        ;;
esac


install_target="."
if [ ${#extras[@]} -gt 0 ]; then
    IFS=','; extra_str="${extras[*]}"; unset IFS
    echo "Installing dependencies: $extra_str"
    install_target=".[${extra_str}]"
else
    echo "Installing dependencies..."
fi

pip install --upgrade -e "$install_target"

for script in "${scripts_to_generate[@]}"; do
    scripts/generate-cmd.sh "$script"
done

echo "Setup completed successfully. To uninstall just delete the directory"

exit 0
