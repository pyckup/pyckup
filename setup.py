from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()
    
setup(
    name="calle",
    version="1.0.1",
    packages=find_packages(),
    install_requires=requirements,
    include_package_data=True,
)
