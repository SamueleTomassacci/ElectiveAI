import torch


def repeat_tensors(tensor, repeat_counts):
    repeated_tensors = [
        tensor[i : i + 1].repeat(repeat, *[1] * (tensor.ndim - 1))
        for i, repeat in enumerate(repeat_counts)
    ]
    if not repeated_tensors:
        return tensor.new_empty((0, *tensor.shape[1:]))
    return torch.cat(repeated_tensors, dim=0)


def split_tensors(tensor, split_counts):
    indices = torch.cumsum(torch.tensor([0] + split_counts), dim=0)
    return [tensor[indices[i] : indices[i + 1]] for i in range(len(split_counts))]


def stack_and_pad(tensor_list):
    max_size = max([t.shape[0] for t in tensor_list])
    padded_list = []
    for tensor in tensor_list:
        if tensor.shape[0] == max_size:
            padded_list.append(tensor)
        else:
            pad = torch.zeros(
                max_size - tensor.shape[0],
                *tensor.shape[1:],
                dtype=tensor.dtype,
                device=tensor.device,
            )
            padded_list.append(torch.cat([tensor, pad], dim=0))
    return torch.stack(padded_list)
