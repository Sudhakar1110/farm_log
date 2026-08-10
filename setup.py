from setuptools import find_packages, setup

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="fleet_log",
    version="0.0.1",
    description="Fleet trip, odometer, fuel log and fuel yield tracking for Frappe v15 (optional ERPNext v15 integration).",
    author="Fleet Log Contributors",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=install_requires,
)
