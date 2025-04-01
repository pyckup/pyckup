# Installing Pyckup
1. Install the package: `pip install git+https://github.com/pyckup/pyckup.git`
2. Install FFMPEG: `sudo apt install ffmpeg`
3. [Install PJSUA2](https://docs.pjsip.org/en/latest/pjsua2/building.html) (there seems to be an issue in newer versions of the library, so checkout [this commit](https://github.com/pjsip/pjproject/commit/f5d890aa3463a096d7110ae935c67d6249d2f662) )
4. Setup OPENAI API environment variable: `export OPENAI_API_KEY=<your_openai_api_key>`   