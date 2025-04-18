from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="pyckup",
    version="1.0.1",
    packages=find_packages(),
    install_requires=requirements,
    package_data={"pyckup": ["resources/*", "conf/*"]},
    include_package_data=True,
)
