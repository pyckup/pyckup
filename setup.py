from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="pyckup-lib",
    version="1.0.7",
    packages=find_packages(),
    install_requires=requirements,
    package_data={"pyckup": ["resources/*", "conf/*"]},
    include_package_data=True,
)
