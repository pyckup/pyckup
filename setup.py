from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()
    
setup(
    name="call_e",
    version="1.0.0",
    packages=find_packages(),
    install_requires=requirements,
    package_data={'call_e': ['resources/*', 'conf/*']},
    include_package_data=True,
)
